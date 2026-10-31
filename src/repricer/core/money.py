"""Денежная арифметика. Целые копейки, проценты — Decimal."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

KOPECKS_PER_RUBLE = 100


def round_to_ruble(kopecks: Decimal, *, up: bool) -> int:
    """Округлить сумму в копейках до целого рубля вверх или вниз."""

    rubles = Decimal(kopecks) / KOPECKS_PER_RUBLE
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    return int(rubles.to_integral_value(rounding=rounding)) * KOPECKS_PER_RUBLE


def margin_pct(price_kopecks: int | Decimal, cost_kopecks: int | Decimal) -> Decimal | None:
    """Маржа в процентах: (цена - себестоимость) / цена."""

    price = Decimal(price_kopecks)
    if price <= 0:
        return None
    return (price - Decimal(cost_kopecks)) / price * Decimal(100)


def quantize_pct(value: Decimal | None) -> Decimal | None:
    """Округлить процент маржи до одного знака для показа."""

    if value is None:
        return None
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def quantize_days(value: Decimal | None) -> Decimal | None:
    """Округлить запас в днях до одного знака для показа."""

    if value is None:
        return None
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def fmt_rubles(kopecks: int | Decimal) -> str:
    """Отформатировать копейки как рубли с запятой, без лишних нулей."""

    rubles = Decimal(kopecks) / KOPECKS_PER_RUBLE
    text = f"{rubles:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def fmt_pct(value: Decimal | None) -> str:
    """Отформатировать процент с одним знаком и запятой."""

    if value is None:
        return "—"
    return f"{value:.1f}".replace(".", ",")
