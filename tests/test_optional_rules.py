"""Необязательные правила из п. 6 ТЗ: поставка в пути и возврат к базовой."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from repricer.core import ProductSnapshot, Reason, Rules, Zone, decide

AS_OF = datetime(2024, 3, 31, 12, 0, 0)
RETURN = Rules(return_to_base=True)


def make_product(
    *,
    stock: str = "300",
    current: int = 140000,
    base: int = 150000,
    cost: int = 50000,
    sold: str = "140",
    last_change: datetime | None = None,
    incoming_qty: str = "0",
    incoming_date=None,
) -> ProductSnapshot:
    return ProductSnapshot(
        vendor_code="SKU",
        name="Товар",
        stock_qty=Decimal(stock),
        cost_kopecks=cost,
        base_price_kopecks=base,
        current_price_kopecks=current,
        sales_by_day=((AS_OF.date(), Decimal(sold)),),
        last_price_change_at=last_change,
        incoming_qty=Decimal(incoming_qty),
        incoming_date=incoming_date,
    )


def test_incoming_before_depletion_suppresses_outage():
    # 20 шт. при 10 шт./день — остаток кончится через 2 дня, поставка через 1 день.
    decision = decide(
        make_product(stock="20", incoming_qty="100", incoming_date=AS_OF.date() + timedelta(days=1)),
        Rules(),
        as_of=AS_OF,
    )

    assert decision.zone is Zone.NORMAL
    assert decision.reason is Reason.INCOMING
    assert decision.recommended_price_kopecks == decision.current_price_kopecks
    assert "incoming" in decision.limiters


def test_incoming_after_depletion_keeps_outage():
    decision = decide(
        make_product(stock="20", incoming_qty="100", incoming_date=AS_OF.date() + timedelta(days=2)),
        Rules(),
        as_of=AS_OF,
    )

    assert decision.zone is Zone.OUTAGE
    assert decision.recommended_price_kopecks == 144200


def test_incoming_on_depletion_day_is_not_earlier():
    # Запас ровно 2 дня: поставка «в день исчерпания» не считается ранней.
    decision = decide(
        make_product(stock="20", incoming_qty="100", incoming_date=AS_OF.date() + timedelta(days=2)),
        Rules(),
        as_of=AS_OF,
    )

    assert decision.reason is Reason.RAISE_OUTAGE


def test_no_stock_has_priority_over_incoming():
    decision = decide(
        make_product(stock="0", incoming_qty="100", incoming_date=AS_OF.date() + timedelta(days=1)),
        Rules(),
        as_of=AS_OF,
    )

    assert decision.zone is Zone.NO_STOCK
    assert decision.reason is Reason.NO_STOCK


def test_return_to_base_steps_up_when_stock_is_normal():
    decision = decide(
        make_product(current=140000),
        RETURN,
        as_of=AS_OF,
    )

    assert decision.zone is Zone.NORMAL
    assert decision.reason is Reason.RETURN_TO_BASE
    assert decision.recommended_price_kopecks == 144200


def test_return_to_base_does_not_overshoot_base():
    decision = decide(
        make_product(current=149000),
        RETURN,
        as_of=AS_OF,
    )

    assert decision.reason is Reason.RETURN_TO_BASE
    assert decision.recommended_price_kopecks == 150000


def test_return_to_base_steps_down_to_base():
    decision = decide(
        make_product(current=153500),
        RETURN,
        as_of=AS_OF,
    )

    assert decision.reason is Reason.RETURN_TO_BASE
    assert decision.recommended_price_kopecks == 150000


def test_return_to_base_is_off_by_default():
    decision = decide(
        make_product(current=140000),
        Rules(),
        as_of=AS_OF,
    )

    assert decision.reason is Reason.NORMAL
    assert decision.recommended_price_kopecks == 140000


def test_return_to_base_respects_min_margin():
    decision = decide(
        make_product(current=155000, base=150000, cost=150000),
        RETURN,
        as_of=AS_OF,
    )

    assert decision.reason is Reason.MIN_MARGIN
    assert decision.recommended_price_kopecks == 155000


def test_return_to_base_respects_pause():
    decision = decide(
        make_product(current=140000, last_change=AS_OF - timedelta(hours=1)),
        RETURN,
        as_of=AS_OF,
    )

    assert decision.reason is Reason.PAUSE
    assert decision.recommended_price_kopecks == 140000