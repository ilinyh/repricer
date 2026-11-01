"""Прикладной сервис: прогон, очередь, отправка, статусы.

Ядро расчёта остаётся чистым; здесь — сборка снимка из адаптеров, запись в БД,
идемпотентность повторных прогонов и работа с площадкой.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterable

from ..adapters.accounting.base import AccountingProduct, AccountingSource
from ..adapters.marketplace.wb_client import WBClient, UploadPrice, PROCESSED_STATUS
from ..core import Decision, Mode, ProductSnapshot, Rules, decide
from ..storage.db import Repository
from .pricing import choose_price_before

RULES_SETTING = "rules"
SCHEDULE_SETTING = "schedule_minutes"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO = "auto"
STATUS_SENT = "sent"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_QUARANTINE = "quarantine"
# Активные решения: ещё не применённые площадкой. Повторный прогон не должен
# создавать по ним дубликат.
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_AUTO, STATUS_SENT)

INT_FIELDS = {"sales_window_days", "pause_hours"}


def coerce_rules(data: dict | None) -> Rules:
    """Собрать :class:`Rules` из словаря со строковыми значениями."""

    base = Rules()
    kwargs: dict[str, object] = {}
    for field in fields(Rules):
        if not data or field.name not in data:
            continue
        raw = data[field.name]
        if raw is None or raw == "":
            continue
        kwargs[field.name] = int(raw) if field.name in INT_FIELDS else Decimal(str(raw))
    return replace(base, **kwargs)


def rules_to_dict(rules: Rules) -> dict[str, str]:
    return {
        field.name: str(getattr(rules, field.name))
        for field in fields(rules)
    }


@dataclass
class Snapshot:
    product: AccountingProduct
    good: object | None
    decision: Decision | None
    mode: str
    rules: Rules


class RepricerService:
    def __init__(
        self,
        repo: Repository,
        accounting: AccountingSource,
        marketplace: WBClient,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.repo = repo
        self.accounting = accounting
        self.marketplace = marketplace
        self.clock = clock

    # --- параметры ------------------------------------------------------
    def global_rules(self) -> Rules:
        return coerce_rules(json.loads(self.repo.get_setting(RULES_SETTING, "{}") or "{}"))

    def save_global_rules(self, data: dict) -> None:
        self.repo.set_setting(RULES_SETTING, json.dumps(data, ensure_ascii=False))

    def schedule_minutes(self) -> int:
        raw = self.repo.get_setting(SCHEDULE_SETTING, "0")
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def set_schedule_minutes(self, value: int) -> None:
        self.repo.set_setting(SCHEDULE_SETTING, str(max(0, int(value))))

    def effective_rules(self, vendor_code: str) -> Rules:
        product = self.repo.get_product(vendor_code)
        merged = rules_to_dict(self.global_rules())
        for key, value in (product.get("overrides") or {}).items():
            if value in (None, ""):
                continue
            merged[key] = str(value)
        return coerce_rules(merged)

    def max_window(self) -> int:
        window = self.global_rules().sales_window_days
        for product in self.repo.list_products().values():
            overrides = product.get("overrides") or {}
            if overrides.get("sales_window_days"):
                window = max(window, int(overrides["sales_window_days"]))
        return window

    # --- снимок ---------------------------------------------------------
    def _snapshot(self, as_of: datetime) -> list[Snapshot]:
        market = {good.vendor_code: good for good in self.marketplace.list_goods()}
        accounting = {
            product.vendor_code: product
            for product in self.accounting.fetch(
                as_of=as_of.date(), window_days=self.max_window()
            )
        }
        products = self.repo.list_products()
        snapshots: list[Snapshot] = []
        for code, product in accounting.items():
            stored = products.get(code, {})
            mode = stored.get("mode", Mode.OFF.value)
            good = market.get(code)
            if good is None:
                snapshots.append(Snapshot(product, None, None, mode, Rules()))
                continue
            if stored.get("nm_id") != getattr(good, "nm_id", None):
                self.repo.upsert_product(
                    code, product.name, getattr(good, "nm_id", None), at=self.clock()
                )
            rules = self.effective_rules(code)
            decision = decide(
                ProductSnapshot(
                    vendor_code=code,
                    name=product.name,
                    stock_qty=product.stock_qty,
                    cost_kopecks=product.cost_kopecks,
                    base_price_kopecks=product.base_price_kopecks,
                    current_price_kopecks=good.discounted_price_kopecks,
                    sales_by_day=product.sales_by_day,
                    last_price_change_at=self.repo.last_applied_at(code, before=as_of),
                ),
                rules,
                as_of=as_of,
                mode=Mode(mode),
            )
            snapshots.append(Snapshot(product, good, decision, mode, rules))
        return snapshots

    # --- прогон ---------------------------------------------------------
    def run(self, *, as_of: datetime | None = None, trigger: str = "manual") -> dict:
        started = self.clock()
        as_of = as_of or started
        run_id = self.repo.create_run(as_of=as_of, trigger=trigger, started_at=started)
        counts = {"products": 0, "changed": 0, "created": 0, "superseded": 0}
        counters = {"off": 0, "no_change": 0, "not_on_market": 0, "same_recommendation": 0, "in_flight": 0}

        try:
            snapshots = self._snapshot(as_of)
        except Exception as exc:  # noqa: BLE001 - сообщение уходит в UI
            self.repo.finish_run(
                run_id, status="failed", finished_at=self.clock(), counters=counts, message=str(exc)
            )
            raise

        for snap in snapshots:
            counts["products"] += 1
            decision = snap.decision
            if decision is None:
                counters["not_on_market"] += 1
                continue
            if not decision.price_changed or decision.recommended_price_kopecks == decision.current_price_kopecks:
                counters["no_change"] += 1
                if snap.mode != Mode.OFF.value:
                    self._journal_no_change(snap, as_of)
                continue

            counts["changed"] += 1
            if snap.mode == Mode.OFF.value:
                counters["off"] += 1
                continue

            existing = self.repo.active_for(snap.product.vendor_code)
            if existing and int(existing["recommended_price_kopecks"]) == decision.recommended_price_kopecks and int(
                existing["current_price_kopecks"]
            ) == decision.current_price_kopecks:
                counters["same_recommendation"] += 1
                continue
            if existing and existing["status"] == STATUS_SENT:
                # Цена уже отправлена и ждёт ответа площадки — вторую не создаём.
                counters["in_flight"] += 1
                continue
            if existing:
                self.repo.supersede_active(snap.product.vendor_code, at=as_of)
                counts["superseded"] += 1
                self.repo.append_journal(
                    "recommendation_superseded",
                    at=as_of,
                    vendor_code=snap.product.vendor_code,
                    payload={"decision_id": existing["id"]},
                )

            status = STATUS_PENDING if snap.mode == Mode.RECOMMEND.value else STATUS_AUTO
            decision_id = self._store_decision(run_id, snap, as_of, status)
            counts["created"] += 1
            self.repo.append_journal(
                "recommendation_created",
                at=as_of,
                vendor_code=snap.product.vendor_code,
                payload={
                    "decision_id": decision_id,
                    "current_price": decision.current_price_kopecks,
                    "recommended_price": decision.recommended_price_kopecks,
                    "reason": decision.reason.value,
                    "mode": snap.mode,
                },
            )

        self.repo.finish_run(
            run_id, status="ok", finished_at=self.clock(), counters=counts, message=json.dumps(counters, ensure_ascii=False)
        )
        return {"run_id": run_id, "as_of": as_of.isoformat(), **counts, **counters}

    def _store_decision(self, run_id: int, snap: Snapshot, as_of: datetime, status: str) -> int:
        decision = snap.decision
        assert decision is not None
        return self.repo.create_decision(
            run_id=run_id,
            vendor_code=snap.product.vendor_code,
            name=snap.product.name,
            nm_id=getattr(snap.good, "nm_id", None),
            as_of=as_of.isoformat(),
            zone=decision.zone.value,
            sales_per_day=str(decision.sales_per_day),
            days_of_stock=str(decision.days_of_stock) if decision.days_of_stock is not None else None,
            current_price_kopecks=decision.current_price_kopecks,
            recommended_price_kopecks=decision.recommended_price_kopecks,
            base_price_kopecks=snap.product.base_price_kopecks,
            cost_kopecks=snap.product.cost_kopecks,
            margin_before=str(decision.margin_before_pct) if decision.margin_before_pct is not None else None,
            margin_after=str(decision.margin_after_pct) if decision.margin_after_pct is not None else None,
            reason=decision.reason.value,
            message=decision.message,
            limiters=json.dumps(list(decision.limiters), ensure_ascii=False),
            margin_warning=1 if decision.margin_warning else 0,
            status=status,
            created_at=as_of.isoformat(),
        )

    def _journal_no_change(self, snap: Snapshot, as_of: datetime) -> None:
        decision = snap.decision
        assert decision is not None
        payload = {
            "price": decision.current_price_kopecks,
            "reason": decision.reason.value,
            "message": decision.message,
        }
        last = self.repo.last_journal_event(snap.product.vendor_code, "no_change")
        if last and last["payload"] == payload:
            return
        self.repo.append_journal(
            "no_change", at=as_of, vendor_code=snap.product.vendor_code, payload=payload
        )

    # --- очередь --------------------------------------------------------
    def approve(self, decision_ids: Iterable[int] | None = None) -> int:
        return self._set_status(
            decision_ids,
            from_statuses=(STATUS_PENDING,),
            to_status=STATUS_APPROVED,
            event="approved",
        )

    def reject(self, decision_ids: Iterable[int] | None = None) -> int:
        return self._set_status(
            decision_ids,
            from_statuses=(STATUS_PENDING, STATUS_AUTO),
            to_status=STATUS_REJECTED,
            event="rejected",
        )

    def _set_status(
        self,
        decision_ids: Iterable[int] | None,
        *,
        from_statuses: tuple[str, ...],
        to_status: str,
        event: str,
    ) -> int:
        if decision_ids is None:
            rows = self.repo.list_decisions(from_statuses)
        else:
            rows = [
                row
                for row in (self.repo.get_decision(int(i)) for i in decision_ids)
                if row and row["status"] in from_statuses
            ]
        now = self.clock()
        for row in rows:
            self.repo.update_decision(row["id"], status=to_status, resolved_at=now.isoformat())
            self.repo.append_journal(
                event,
                at=now,
                vendor_code=row["vendor_code"],
                payload={"decision_id": row["id"]},
            )
        return len(rows)

    # --- отправка и статусы ---------------------------------------------
    def send(self, decision_ids: Iterable[int] | None = None) -> dict:
        if decision_ids is None:
            rows = self.repo.list_decisions((STATUS_APPROVED, STATUS_AUTO))
        else:
            rows = [
                row
                for row in (self.repo.get_decision(int(i)) for i in decision_ids)
                if row and row["status"] in (STATUS_APPROVED, STATUS_AUTO)
            ]
        if not rows:
            return {"sent": 0, "failed": 0, "message": "Нет решений к отправке"}

        now = self.clock()
        market = {good.vendor_code: good for good in self.marketplace.list_goods()}
        prepared: list[tuple[dict, int, int]] = []
        failed = 0

        for row in rows:
            good = market.get(row["vendor_code"])
            if good is None:
                self._fail(row, now, "unknown_nm", "Товар не найден на площадке")
                failed += 1
                continue
            rules = self.effective_rules(row["vendor_code"])
            chosen = choose_price_before(
                int(row["recommended_price_kopecks"]),
                good.discount,
                base_price_kopecks=int(row["base_price_kopecks"]),
                cost_kopecks=int(row["cost_kopecks"]),
                current_price_kopecks=int(row["current_price_kopecks"]),
                rules=rules,
            )
            if chosen is None:
                self._fail(row, now, "limits", "Не удалось попасть в коридор и маржу")
                failed += 1
                continue
            price_rubles, actual = chosen
            if actual == int(row["current_price_kopecks"]):
                self._fail(row, now, "no_change", "Фактическая цена не изменилась")
                failed += 1
                continue
            prepared.append((row, price_rubles, actual))

        if not prepared:
            return {"sent": 0, "failed": failed, "message": "Отправлять нечего"}

        uploads = [
            UploadPrice(nm_id=int(row["nm_id"]), price_rubles=price_rubles, discount=market[row["vendor_code"]].discount)
            for row, price_rubles, _ in prepared
        ]
        upload_id, already_exists = self.marketplace.upload_task(uploads)
        self.repo.save_task(upload_id, 1, at=now)

        for row, price_rubles, actual in prepared:
            discount = market[row["vendor_code"]].discount
            self.repo.update_decision(
                row["id"],
                status=STATUS_SENT,
                upload_id=upload_id,
                sent_price_rubles=price_rubles,
                sent_discount=discount,
                actual_price_kopecks=actual,
            )
            self.repo.append_journal(
                "sent",
                at=now,
                vendor_code=row["vendor_code"],
                payload={
                    "decision_id": row["id"],
                    "upload_id": upload_id,
                    "price_rubles": price_rubles,
                    "discount": discount,
                    "actual_price_kopecks": actual,
                },
            )

        return {
            "sent": len(prepared),
            "failed": failed,
            "upload_id": upload_id,
            "alreadyExists": already_exists,
        }

    def _fail(self, row: dict, at: datetime, code: str, text: str) -> None:
        self.repo.update_decision(
            row["id"],
            status=STATUS_FAILED,
            resolved_at=at.isoformat(),
            error_code=code,
            error_text=text,
        )
        self.repo.append_journal(
            "failed",
            at=at,
            vendor_code=row["vendor_code"],
            payload={"decision_id": row["id"], "error": text},
        )

    def poll(self) -> dict:
        now = self.clock()
        processed_tasks = 0
        updated = 0
        for task in self.repo.tasks_with_status(1):
            status = self.marketplace.task_status(int(task["upload_id"]))
            self.repo.set_task_status(int(task["upload_id"]), status.status, at=now)
            if status.status != PROCESSED_STATUS:
                continue
            processed_tasks += 1
            decisions: dict[int, list[dict]] = {}
            for row in self.repo.decisions_by_upload(int(task["upload_id"]), (STATUS_SENT,)):
                decisions.setdefault(int(row["nm_id"]), []).append(row)
            for item in status.items:
                for row in decisions.get(item.nm_id, []):
                    text = item.error_text or ""
                    if item.error_code == "quarantine" or "арантин" in text:
                        new_status, event = STATUS_QUARANTINE, "quarantine"
                    elif text:
                        new_status, event = STATUS_FAILED, "failed"
                    else:
                        new_status, event = STATUS_APPLIED, "applied"
                    actual = item.price_rubles * (100 - item.discount)
                    self.repo.update_decision(
                        row["id"],
                        status=new_status,
                        resolved_at=now.isoformat(),
                        error_code=item.error_code,
                        error_text=text,
                        actual_price_kopecks=actual,
                    )
                    self.repo.append_journal(
                        event,
                        at=now,
                        vendor_code=row["vendor_code"],
                        payload={
                            "decision_id": row["id"],
                            "upload_id": int(task["upload_id"]),
                            "price_kopecks": actual,
                            "error": text,
                        },
                    )
                    updated += 1
        return {"tasks": processed_tasks, "decisions": updated}

    # --- представление --------------------------------------------------
    def table(self, *, as_of: datetime | None = None) -> list[dict]:
        as_of = as_of or self.clock()
        rows: list[dict] = []
        for snap in self._snapshot(as_of):
            decision = snap.decision
            pending = self.repo.pending_for(snap.product.vendor_code)
            rows.append(
                {
                    "vendor_code": snap.product.vendor_code,
                    "name": snap.product.name,
                    "stock_qty": snap.product.stock_qty,
                    "zone": decision.zone.value if decision else "not_on_market",
                    "sales_per_day": decision.sales_per_day if decision else None,
                    "days_of_stock": decision.days_of_stock if decision else None,
                    "base_price_kopecks": snap.product.base_price_kopecks,
                    "current_price_kopecks": decision.current_price_kopecks if decision else None,
                    "recommended_price_kopecks": decision.recommended_price_kopecks if decision else None,
                    "margin_before": decision.margin_before_pct if decision else None,
                    "margin_after": decision.margin_after_pct if decision else None,
                    "reason": decision.reason.value if decision else "",
                    "message": decision.message if decision else "Товар не найден на площадке",
                    "limiters": list(decision.limiters) if decision else [],
                    "margin_warning": decision.margin_warning if decision else False,
                    "price_changed": decision.price_changed if decision else False,
                    "mode": snap.mode,
                    "pending_id": pending["id"] if pending else None,
                    "pending_status": pending["status"] if pending else None,
                }
            )
        return rows

    def queue(self) -> list[dict]:
        """Решения, которые ещё не отправлены: ждут подтверждения и авто."""
        return self.repo.list_decisions((STATUS_PENDING, STATUS_AUTO))

    def journal(self, vendor_code: str | None = None) -> list[dict]:
        return self.repo.journal(vendor_code=vendor_code)
