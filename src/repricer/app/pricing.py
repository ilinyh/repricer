"""Выбор цены до скидки под целевую цену после скидки.

Модель WB: итоговая цена = цена_до_скидки × (1 − скидка/100), обе величины целые.
Держим скидку неизменной и подбираем цену до скидки так, чтобы фактическая цена
была как можно ближе к рекомендации и не выходила за коридор и минимальную маржу.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from ..core import margin_pct
from ..core.types import Rules


def corridor(rules: Rules, base_price_kopecks: int) -> tuple[Decimal, Decimal]:
    lower = Decimal(base_price_kopecks) * (Decimal(100) - rules.max_discount_pct) / Decimal(100)
    upper = Decimal(base_price_kopecks) * (Decimal(100) + rules.max_markup_pct) / Decimal(100)
    return lower, upper


def choose_price_before(
    target_kopecks: int,
    discount: int,
    *,
    base_price_kopecks: int,
    cost_kopecks: int,
    current_price_kopecks: int,
    rules: Rules,
) -> tuple[int, int] | None:
    """Вернуть (цена до скидки в рублях, фактическая цена в копейках) или None."""

    factor = 100 - discount
    if factor <= 0:
        return None

    exact = Decimal(target_kopecks) / Decimal(factor)
    candidates = {
        int(exact.to_integral_value(rounding=ROUND_FLOOR)),
        int(exact.to_integral_value(rounding=ROUND_CEILING)),
    }
    candidates |= {min(candidates) - 1, max(candidates) + 1}

    lower, upper = corridor(rules, base_price_kopecks)
    lowering = target_kopecks < current_price_kopecks

    best: tuple[tuple[Decimal, int], int, int] | None = None
    for price_rubles in sorted(c for c in candidates if c > 0):
        actual = price_rubles * factor
        if Decimal(actual) < lower or Decimal(actual) > upper:
            continue
        if lowering:
            margin = margin_pct(actual, cost_kopecks)
            if margin is not None and margin < rules.min_margin_pct:
                continue
        key = (abs(Decimal(actual) - Decimal(target_kopecks)), price_rubles)
        if best is None or key < best[0]:
            best = (key, price_rubles, actual)

    if best is None:
        return None
    return best[1], best[2]
