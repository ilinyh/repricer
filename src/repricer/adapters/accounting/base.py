"""Контракт источника учётных данных.

Ядро расчёта не знает, откуда пришли данные. Новый источник — новый адаптер,
ядро и остальное приложение не меняются.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AccountingProduct:
    """Товар из учётной системы на дату расчёта."""

    vendor_code: str
    name: str
    stock_qty: Decimal
    cost_kopecks: int
    base_price_kopecks: int
    sales_by_day: tuple[tuple[date, Decimal], ...]
    incoming_qty: Decimal = Decimal(0)
    incoming_date: date | None = None
    returns_by_day: tuple[tuple[date, Decimal], ...] = ()


@runtime_checkable
class AccountingSource(Protocol):
    """Источник учётных данных."""

    def fetch(self, *, as_of: date, window_days: int) -> list[AccountingProduct]:
        """Вернуть товары с остатками, себестоимостью, базовой ценой и продажами.

        Продажи — за ``window_days`` дней, заканчивающихся датой ``as_of`` включительно.
        Реализация обязана вернуть не меньше дней, чем запрошено.
        """
        ...
