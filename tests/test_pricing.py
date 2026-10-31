"""Подбор цены до скидки под рекомендацию."""

from __future__ import annotations

from decimal import Decimal

from repricer.app import choose_price_before
from repricer.core.types import Rules


def test_picks_closest_effective_price():
    price, actual = choose_price_before(
        153500,
        10,
        base_price_kopecks=150000,
        cost_kopecks=50000,
        current_price_kopecks=149000,
        rules=Rules(),
    )
    assert price == 1706
    assert actual == 153540  # 1706 × 0,9


def test_no_discount_matches_target_exactly():
    price, actual = choose_price_before(
        153500,
        0,
        base_price_kopecks=150000,
        cost_kopecks=50000,
        current_price_kopecks=149000,
        rules=Rules(),
    )
    assert (price, actual) == (1535, 153500)


def test_returns_none_outside_corridor():
    assert (
        choose_price_before(
            90000,
            0,
            base_price_kopecks=150000,
            cost_kopecks=10000,
            current_price_kopecks=100000,
            rules=Rules(),
        )
        is None
    )


def test_respects_min_margin_when_lowering():
    rules = Rules(min_margin_pct=Decimal("50"))
    assert (
        choose_price_before(
            130000,
            0,
            base_price_kopecks=150000,
            cost_kopecks=80000,
            current_price_kopecks=140000,
            rules=rules,
        )
        is None
    )


def test_impossible_discount():
    assert (
        choose_price_before(
            100000,
            100,
            base_price_kopecks=150000,
            cost_kopecks=10000,
            current_price_kopecks=120000,
            rules=Rules(),
        )
        is None
    )
