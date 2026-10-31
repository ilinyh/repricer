"""Контрольные примеры из приложения А ТЗ — должны сходиться один в один."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from repricer.core import Mode, ProductSnapshot, Reason, Rules, Zone, decide

AS_OF = datetime(2023, 10, 24, 12, 0, 0)
RULES = Rules()

# cost, base, current, stock, sold за 14 дней, запас (дн.), зона, новая цена, маржа до, маржа после, причина
CASES = [
    (1, 500, 1500, 1500, 300, 140, "30", Zone.NORMAL, 1500, "66.7", "66.7", Reason.NORMAL),
    (2, 500, 1500, 1490, 50, 140, "5", Zone.OUTAGE, 1535, "66.4", "67.4", Reason.RAISE_OUTAGE),
    (3, 500, 1500, 1333, 900, 140, "90", Zone.OVERSTOCK, 1293, "62.5", "61.3", Reason.LOWER_OVERSTOCK),
    (4, 500, 1500, 1060, 900, 140, "90", Zone.OVERSTOCK, 1050, "52.8", "52.4", Reason.LOWER_OVERSTOCK),
    (5, 1150, 1500, 1380, 900, 140, "90", Zone.OVERSTOCK, 1380, "16.7", "16.7", Reason.MIN_MARGIN),
    (6, 1300, 1500, 1400, 300, 140, "30", Zone.NORMAL, 1400, "7.1", "7.1", Reason.NORMAL),
    (7, 500, 1500, 1500, 40, 0, None, Zone.OVERSTOCK, 1455, "66.7", "65.6", Reason.NO_SALES_OVERSTOCK),
    (8, 500, 1500, 1500, 0, 140, None, Zone.NO_STOCK, 1500, None, None, Reason.NO_STOCK),
    (9, 500, 1500, 8900, 50, 140, "5", Zone.OUTAGE, 9000, "94.4", "94.4", Reason.RAISE_OUTAGE),
    (10, 500, 1500, 9000, 50, 140, "5", Zone.OUTAGE, 9000, "94.4", "94.4", Reason.CORRIDOR_UPPER),
    (11, 1300, 1500, 1410, 50, 140, "5", Zone.OUTAGE, 1453, "7.8", "10.5", Reason.RAISE_OUTAGE),
    (12, 500, 1500, 1500, 140, 140, "14", Zone.NORMAL, 1500, "66.7", "66.7", Reason.NORMAL),
    (13, 500, 1500, 1500, 600, 140, "60", Zone.NORMAL, 1500, "66.7", "66.7", Reason.NORMAL),
    (14, 500, 1500, 1500, 601, 140, "60.1", Zone.OVERSTOCK, 1455, "66.7", "65.6", Reason.LOWER_OVERSTOCK),
]

LIMITERS = {
    4: "corridor_lower",
    5: "min_margin",
    9: "corridor_upper",
    10: "corridor_upper",
}

WARNINGS = {6, 11}


def make_product(cost, base, current, stock, sold, *, last_change=None) -> ProductSnapshot:
    sales = () if sold == 0 else ((AS_OF.date(), Decimal(sold)),)
    return ProductSnapshot(
        vendor_code="SKU",
        name="Товар",
        stock_qty=Decimal(stock),
        cost_kopecks=cost * 100,
        base_price_kopecks=base * 100,
        current_price_kopecks=current * 100,
        sales_by_day=sales,
        last_price_change_at=last_change,
    )


@pytest.mark.parametrize(
    "num,cost,base,current,stock,sold,days,zone,new_price,margin_before,margin_after,reason",
    CASES,
    ids=[f"A{c[0]}" for c in CASES],
)
def test_control_examples(
    num, cost, base, current, stock, sold, days, zone, new_price, margin_before, margin_after, reason
):
    decision = decide(make_product(cost, base, current, stock, sold), RULES, as_of=AS_OF)

    assert decision.zone is zone
    assert decision.days_of_stock == (None if days is None else Decimal(days))
    assert decision.recommended_price_kopecks == new_price * 100
    assert decision.price_changed is (new_price != current)
    assert decision.reason is reason
    assert decision.margin_before_pct == (None if margin_before is None else Decimal(margin_before))
    assert decision.margin_after_pct == (None if margin_after is None else Decimal(margin_after))
    assert decision.margin_warning is (num in WARNINGS)
    if num in LIMITERS:
        assert LIMITERS[num] in decision.limiters


def test_pause_blocks_change():
    """Товар из примера 2, но цену меняли 10 часов назад."""

    product = make_product(500, 1500, 1490, 50, 140, last_change=AS_OF - timedelta(hours=10))
    decision = decide(product, RULES, as_of=AS_OF)

    assert decision.reason is Reason.PAUSE
    assert decision.recommended_price_kopecks == 1490 * 100
    assert decision.price_changed is False
    assert "pause" in decision.limiters


def test_pause_expires_after_24h():
    product = make_product(500, 1500, 1490, 50, 140, last_change=AS_OF - timedelta(hours=24))
    decision = decide(product, RULES, as_of=AS_OF)

    assert decision.reason is Reason.RAISE_OUTAGE
    assert decision.recommended_price_kopecks == 1535 * 100


def test_sales_outside_window_ignored():
    product = ProductSnapshot(
        vendor_code="SKU",
        name="Товар",
        stock_qty=Decimal(200),
        cost_kopecks=500 * 100,
        base_price_kopecks=1500 * 100,
        current_price_kopecks=1500 * 100,
        sales_by_day=(
            (AS_OF.date() - timedelta(days=20), Decimal(1000)),
            (AS_OF.date(), Decimal(140)),
        ),
    )
    decision = decide(product, RULES, as_of=AS_OF)

    assert decision.days_of_stock == Decimal("20.0")
    assert decision.zone is Zone.NORMAL


def test_small_step_still_changes_price():
    """Дешёвый товар: шаг меньше полурубля не должен «застревать»."""

    overstock = make_product(100, 1000, 1000, 100, 14)
    decision = decide(overstock, RULES, as_of=AS_OF)
    assert decision.recommended_price_kopecks == 970 * 100

    short = make_product(100, 1000, 1000, 1, 14)
    decision = decide(short, RULES, as_of=AS_OF)
    assert decision.zone is Zone.OUTAGE
    assert decision.recommended_price_kopecks == 1030 * 100


def test_fractional_corridor_bound_rounds_inside():
    """Нижняя граница коридора нецелая — округляем внутрь, вверх."""

    product = ProductSnapshot(
        vendor_code="SKU",
        name="Товар",
        stock_qty=Decimal(900),
        cost_kopecks=500 * 100,
        base_price_kopecks=149999,  # 1499,99 ₽ → нижняя граница 1049,993 ₽
        current_price_kopecks=1060 * 100,
        sales_by_day=((AS_OF.date(), Decimal(140)),),
    )
    decision = decide(product, RULES, as_of=AS_OF)

    assert "corridor_lower" in decision.limiters
    assert decision.recommended_price_kopecks == 1050 * 100


def test_mode_does_not_affect_arithmetic():
    product = make_product(500, 1500, 1490, 50, 140)
    results = [decide(product, RULES, as_of=AS_OF, mode=mode) for mode in Mode]
    assert {r.recommended_price_kopecks for r in results} == {1535 * 100}


def test_pause_hours_override():
    rules = Rules(pause_hours=1)
    product = make_product(500, 1500, 1490, 50, 140, last_change=AS_OF - timedelta(hours=10))
    decision = decide(product, rules, as_of=AS_OF)

    assert decision.reason is Reason.RAISE_OUTAGE


def test_custom_thresholds_override():
    rules = Rules(outage_days=Decimal("5"), overstock_days=Decimal("20"))
    # 10 дней запаса: при границах 5/20 — норма, при 14/60 — норма.
    product = make_product(500, 1500, 1500, 100, 140)
    assert decide(product, rules, as_of=AS_OF).zone is Zone.NORMAL

    # 30 продаж в день → 3,3 дня: аутсток при границе 5.
    product = make_product(500, 1500, 1500, 100, 420)
    assert decide(product, rules, as_of=AS_OF).zone is Zone.OUTAGE


def test_uses_date_parameter_not_system_clock():
    """Расчёт на историческую дату не должен зависеть от текущих часов."""

    product = ProductSnapshot(
        vendor_code="SKU",
        name="Товар",
        stock_qty=Decimal(300),
        cost_kopecks=500 * 100,
        base_price_kopecks=1500 * 100,
        current_price_kopecks=1500 * 100,
        sales_by_day=((date(2022, 5, 1), Decimal(140)),),
    )

    same_day = decide(product, RULES, as_of=datetime(2022, 5, 1, 12, 0, 0))
    later = decide(product, RULES, as_of=datetime(2022, 6, 1, 12, 0, 0))

    assert same_day.zone is Zone.NORMAL
    assert later.zone is Zone.OVERSTOCK
