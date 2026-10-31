"""Веб-интерфейс: таблица товаров, очередь, карточка, настройки."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..adapters.accounting import CsvAccountingSource, OnecODataSource
from ..adapters.marketplace import WBClient
from ..app import RepricerService
from ..storage import Repository
from .format import (
    EVENT_LABELS,
    LIMITER_LABELS,
    MODE_LABELS,
    REASON_LABELS,
    STATUS_LABELS,
    ZONE_LABELS,
    days,
    label,
    num,
    pct,
    rub,
)

BASE_DIR = Path(__file__).resolve().parent
RULE_FIELDS = (
    "sales_window_days",
    "outage_days",
    "overstock_days",
    "step_pct",
    "min_margin_pct",
    "max_discount_pct",
    "max_markup_pct",
    "pause_hours",
)


def build_service() -> RepricerService:
    db = Path(os.getenv("REPRICER_DB", "data/repricer.db"))
    db.parent.mkdir(parents=True, exist_ok=True)
    repo = Repository(db)
    if os.getenv("ACCOUNTING_SOURCE", "csv").lower() == "1c":
        accounting = OnecODataSource(
            os.getenv("ONEC_URL", "http://localhost:8081/ut115"),
            user=os.getenv("ONEC_USER", "Администратор"),
            password=os.getenv("ONEC_PASSWORD", ""),
            cost_price_type=os.getenv("ONEC_COST_PRICE_TYPE", "Себестоимость"),
            base_price_type=os.getenv("ONEC_BASE_PRICE_TYPE", "Розничная"),
        )
    else:
        accounting = CsvAccountingSource(
            Path(os.getenv("CSV_PRODUCTS", "data/products.csv")),
            Path(os.getenv("CSV_SALES", "data/sales.csv")),
        )
    marketplace = WBClient(
        os.getenv("WB_BASE_URL", "http://localhost:8090"),
        os.getenv("WB_TOKEN", "local-token"),
    )
    return RepricerService(repo, accounting, marketplace)


def create_app(service: RepricerService | None = None, *, as_of_default: str | None = None) -> FastAPI:
    service = service or build_service()
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.globals.update(
        rub=rub,
        pct=pct,
        days=days,
        num=num,
        zone_label=lambda value: label(ZONE_LABELS, value),
        mode_label=lambda value: label(MODE_LABELS, value),
        status_label=lambda value: label(STATUS_LABELS, value),
        reason_label=lambda value: label(REASON_LABELS, value),
        limiter_label=lambda value: label(LIMITER_LABELS, value),
        event_label=lambda value: label(EVENT_LABELS, value),
    )

    app = FastAPI(title="Репрайсер")
    app.state.service = service
    app.state.as_of_default = (
        as_of_default or os.getenv("REPRICER_AS_OF") or date.today().isoformat()
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    def _as_of(request: Request) -> datetime:
        raw = request.query_params.get("as_of") or request.app.state.as_of_default
        parsed = date.fromisoformat(raw)
        return datetime(parsed.year, parsed.month, parsed.day, 12, 0, 0)

    def _context(request: Request, **extra: object) -> dict:
        last = service.repo.last_run()
        return {
            "pending_count": len(service.queue()),
            "as_of": request.query_params.get("as_of") or request.app.state.as_of_default,
            "last_run": last["finished_at"] if last else "—",
            "message": request.query_params.get("message"),
            **extra,
        }

    def _redirect(message: str) -> RedirectResponse:
        return RedirectResponse(url=f"/?message={message}", status_code=303)

    @app.get("/")
    def index(request: Request):
        rows = service.table(as_of=_as_of(request))
        zone = request.query_params.get("zone") or ""
        mode = request.query_params.get("mode") or ""
        if zone:
            rows = [row for row in rows if row["zone"] == zone]
        if mode:
            rows = [row for row in rows if row["mode"] == mode]
        return templates.TemplateResponse(
            request,
            "index.html",
            _context(request, rows=rows, zone=zone, mode=mode),
        )

    @app.post("/run")
    def run_now(as_of: str = Form(...)):
        try:
            result = service.run(
                as_of=datetime.fromisoformat(as_of).replace(hour=12), trigger="manual"
            )
            message = f"Прогон: товаров {result['products']}, изменений {result['changed']}, новых рекомендаций {result['created']}"
        except Exception as exc:  # noqa: BLE001 - показываем человеку
            message = f"Ошибка прогона: {exc}"
        return _redirect(message)

    @app.post("/send")
    def send_now():
        result = service.send()
        return _redirect(
            f"Отправлено: {result.get('sent', 0)}, ошибок: {result.get('failed', 0)}"
        )

    @app.post("/poll")
    def poll_now():
        result = service.poll()
        return _redirect(
            f"Обновлено задач: {result['tasks']}, решений: {result['decisions']}"
        )

    @app.get("/queue")
    def queue(request: Request):
        rows = [
            dict(row, name=service.repo.get_product(row["vendor_code"])["name"])
            for row in service.queue()
        ]
        return templates.TemplateResponse(request, "queue.html", _context(request, rows=rows))

    @app.post("/queue/decision")
    async def queue_decision(request: Request):
        form = await request.form()
        action = str(form.get("action"))
        raw_ids = form.get("ids") or ""
        if raw_ids == "all":
            ids = None
        else:
            ids = [int(value) for value in str(raw_ids).replace(",", " ").split()]
        if action == "approve":
            count = service.approve(ids)
            return _redirect(f"Подтверждено: {count}")
        count = service.reject(ids)
        return _redirect(f"Отклонено: {count}")

    @app.get("/product/{vendor_code}")
    def product(request: Request, vendor_code: str):
        rows = service.table(as_of=_as_of(request))
        row = next((item for item in rows if item["vendor_code"] == vendor_code), None)
        if row is None:
            return _redirect("Товар не найден")
        return templates.TemplateResponse(
            request,
            "product.html",
            _context(
                request,
                row=row,
                rules=service.effective_rules(vendor_code),
                product=service.repo.get_product(vendor_code),
                journal=service.journal(vendor_code),
            ),
        )

    @app.post("/product/{vendor_code}/mode")
    def set_mode(vendor_code: str, mode: str = Form(...)):
        service.repo.set_mode(vendor_code, mode, at=service.clock())
        service.repo.append_journal(
            "mode_changed", at=service.clock(), vendor_code=vendor_code, payload={"mode": mode}
        )
        return _redirect(f"Режим {vendor_code}: {label(MODE_LABELS, mode)}")

    @app.post("/product/{vendor_code}/overrides")
    async def set_overrides(vendor_code: str, request: Request):
        form = await request.form()
        overrides = {
            field: str(form.get(field))
            for field in RULE_FIELDS
            if form.get(field) not in (None, "")
        }
        service.repo.set_overrides(vendor_code, overrides, at=service.clock())
        return _redirect(f"Переопределения обновлены: {vendor_code}")

    @app.get("/settings")
    def settings(request: Request):
        return templates.TemplateResponse(
            request,
            "settings.html",
            _context(request, rules=service.global_rules()),
        )

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        data = {field: str(form.get(field)) for field in RULE_FIELDS if form.get(field) not in (None, "")}
        service.save_global_rules(data)
        service.repo.append_journal("settings_changed", at=service.clock(), payload=data)
        return _redirect("Настройки сохранены")

    return app
