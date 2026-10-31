"""Хранилище товаров и задач эмулятора. В памяти, с блокировкой."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

STATUS_PROCESSING = 1
STATUS_PROCESSED = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Good:
    """Товар площадки. Цена — целые рубли до скидки, скидка — проценты."""

    nm_id: int
    vendor_code: str
    price: int
    discount: int

    @property
    def price_kopecks(self) -> int:
        return self.price * 100

    @property
    def discounted_price_kopecks(self) -> int:
        # price_руб * (1 - discount/100) * 100 копеек = price_руб * (100 - discount)
        return self.price * (100 - self.discount)


@dataclass
class TaskItem:
    nm_id: int
    price: int
    discount: int
    applied: bool = False
    error_code: str | None = None
    error_text: str = ""


@dataclass
class Task:
    id: int
    created_at: datetime
    items: list[TaskItem] = field(default_factory=list)
    status: int = STATUS_PROCESSING
    applied_at: datetime | None = None


class Store:
    def __init__(
        self,
        goods: list[Good],
        *,
        apply_delay: float = 3.0,
        quarantine_pct: Decimal = Decimal("30"),
    ) -> None:
        self._lock = threading.RLock()
        self._goods: dict[int, Good] = {good.nm_id: good for good in goods}
        self._tasks: dict[int, Task] = {}
        self._next_id = 1
        self._timers: list[threading.Timer] = []
        self.apply_delay = max(0.0, float(apply_delay))
        self.quarantine_pct = Decimal(quarantine_pct)

    # --- товары ---------------------------------------------------------
    def list_goods(
        self,
        *,
        limit: int = 1000,
        offset: int = 0,
        nm_id: int | None = None,
        vendor_code: str | None = None,
    ) -> list[Good]:
        with self._lock:
            goods = list(self._goods.values())
        if nm_id is not None:
            goods = [good for good in goods if good.nm_id == nm_id]
        if vendor_code:
            goods = [good for good in goods if good.vendor_code == vendor_code]
        goods.sort(key=lambda good: good.nm_id)
        return goods[offset : offset + limit]

    # --- задачи ---------------------------------------------------------
    def create_task(self, items: list[TaskItem]) -> tuple[Task, bool]:
        """Создать задачу. Если такая же ещё в обработке — вернуть её."""

        signature = tuple((item.nm_id, item.price, item.discount) for item in items)
        with self._lock:
            for task in self._tasks.values():
                if task.status != STATUS_PROCESSING:
                    continue
                existing = tuple(
                    (item.nm_id, item.price, item.discount) for item in task.items
                )
                if existing == signature:
                    return task, True

            task = Task(id=self._next_id, created_at=_now(), items=list(items))
            self._next_id += 1
            self._tasks[task.id] = task

            timer = threading.Timer(self.apply_delay, self.apply_task, args=(task.id,))
            timer.daemon = True
            self._timers.append(timer)
            timer.start()
            return task, False

    def apply_task(self, task_id: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != STATUS_PROCESSING:
                return

            for item in task.items:
                good = self._goods.get(item.nm_id)
                if good is None:
                    item.error_code = "unknown_nm"
                    item.error_text = f"Товар с nmID {item.nm_id} не найден"
                    continue
                if item.price <= 0:
                    item.error_code = "invalid_price"
                    item.error_text = "Некорректная цена: должна быть больше нуля"
                    continue
                if not 0 <= item.discount <= 100:
                    item.error_code = "invalid_discount"
                    item.error_text = "Некорректная скидка: допустимо от 0 до 100"
                    continue
                if self._is_quarantine(good, item):
                    item.error_code = "quarantine"
                    item.error_text = (
                        "Карантин: новая цена со скидкой ниже старой более чем на "
                        f"{self.quarantine_pct}%"
                    )
                    continue

                good.price = item.price
                good.discount = item.discount
                item.applied = True

            task.status = STATUS_PROCESSED
            task.applied_at = _now()

    def _is_quarantine(self, good: Good, item: TaskItem) -> bool:
        new_effective = Decimal(item.price * (100 - item.discount))
        old_effective = Decimal(good.discounted_price_kopecks)
        threshold = old_effective * (Decimal(100) - self.quarantine_pct) / Decimal(100)
        return new_effective < threshold

    def get_task(self, task_id: int) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def task_goods(self, task: Task, *, limit: int = 1000, offset: int = 0) -> list[TaskItem]:
        with self._lock:
            return task.items[offset : offset + limit]

    def vendor_code(self, nm_id: int) -> str:
        with self._lock:
            good = self._goods.get(nm_id)
            return good.vendor_code if good else ""


def load_goods(path: Path) -> list[Good]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Good(
            nm_id=int(row["nmID"]),
            vendor_code=str(row["vendorCode"]),
            price=int(row["price"]),
            discount=int(row["discount"]),
        )
        for row in payload.get("goods", [])
    ]