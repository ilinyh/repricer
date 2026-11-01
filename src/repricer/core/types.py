"""Типы ядра репрайсера. Никаких зависимостей от ввода-вывода."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Zone(str, Enum):
    """Зона товара по запасу в днях."""

    NO_STOCK = "no_stock"
    OUTAGE = "outage"
    NORMAL = "normal"
    OVERSTOCK = "overstock"


class Mode(str, Enum):
    """Режим работы репрайсера на товаре."""

    OFF = "off"
    RECOMMEND = "recommend"
    AUTO = "auto"


class Reason(str, Enum):
    """Код причины решения."""

    NO_STOCK = "no_stock"
    NO_SALES_OVERSTOCK = "no_sales_overstock"
    NORMAL = "normal"
    RAISE_OUTAGE = "raise_outage"
    LOWER_OVERSTOCK = "lower_overstock"
    CORRIDOR_LOWER = "corridor_lower"
    CORRIDOR_UPPER = "corridor_upper"
    MIN_MARGIN = "min_margin"
    NO_CHANGE = "no_change"
    PAUSE = "pause"
    INCOMING = "incoming"
    RETURN_TO_BASE = "return_to_base"


@dataclass(frozen=True)
class Rules:
    """Параметры правил. Глобальные значения по умолчанию из ТЗ."""

    sales_window_days: int = 14
    outage_days: Decimal = Decimal("14")
    overstock_days: Decimal = Decimal("60")
    step_pct: Decimal = Decimal("3")
    min_margin_pct: Decimal = Decimal("15")
    max_discount_pct: Decimal = Decimal("30")
    max_markup_pct: Decimal = Decimal("500")
    pause_hours: int = 24
    return_to_base: bool = False


@dataclass(frozen=True)
class ProductSnapshot:
    """Снимок товара на дату расчёта. Все цены — целые копейки."""

    vendor_code: str
    name: str
    stock_qty: Decimal
    cost_kopecks: int
    base_price_kopecks: int
    current_price_kopecks: int
    sales_by_day: tuple[tuple[date, Decimal], ...] = ()
    last_price_change_at: datetime | None = None
    incoming_qty: Decimal = Decimal(0)
    incoming_date: date | None = None


@dataclass(frozen=True)
class Decision:
    """Решение по товару."""

    vendor_code: str
    zone: Zone
    sales_per_day: Decimal
    days_of_stock: Decimal | None
    current_price_kopecks: int
    recommended_price_kopecks: int | None
    margin_before_pct: Decimal | None
    margin_after_pct: Decimal | None
    reason: Reason
    message: str
    limiters: tuple[str, ...] = ()
    margin_warning: bool = False
    price_changed: bool = False
