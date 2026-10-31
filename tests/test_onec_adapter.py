"""Адаптер 1С: чистая сборка товаров и живой smoke-тест."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from repricer.adapters.accounting import OnecODataSource, assemble_products

ONEC_URL = "http://127.0.0.1:8081/ut115"


def test_assemble_products():
    products = assemble_products(
        nomenclatures=[
            {"Ref_Key": "n1", "Артикул": "A-1", "Наименование": "Товар 1"},
            {"Ref_Key": "n2", "Артикул": "A-2", "Наименование": "Товар 2"},
            {"Ref_Key": "n3", "Артикул": "A-1", "Наименование": "Дубликат"},
        ],
        balances={"n1": Decimal(100)},
        sales_rows=[
            {
                "АналитикаУчетаНоменклатуры_Key": "k1",
                "Period": "2024-03-31T10:00:00",
                "Количество": 14,
                "Стоимость": 7000,
            }
        ],
        key_to_nom={"k1": "n1"},
        prices={
            "n1": {"Розничная": Decimal(150000), "Себестоимость": Decimal(0)},
            "n2": {"Розничная": Decimal(200000)},
        },
        type_names={},
        cost_type="Себестоимость",
        base_type="Розничная",
        window_days=14,
    )

    assert [product.vendor_code for product in products] == ["A-1", "A-2"]
    first, second = products
    assert first.stock_qty == Decimal(100)
    assert first.base_price_kopecks == 150000
    # Себестоимость из продаж: 7000 / 14 = 500 ₽.
    assert first.cost_kopecks == 50000
    assert first.sales_by_day == ((date(2024, 3, 31), Decimal(14)),)
    assert second.stock_qty == Decimal(0)
    assert second.cost_kopecks == 0


def test_explicit_cost_price_wins():
    products = assemble_products(
        nomenclatures=[{"Ref_Key": "n1", "Артикул": "A-1", "Наименование": "Товар"}],
        balances={},
        sales_rows=[],
        key_to_nom={},
        prices={"n1": {"Розничная": Decimal(150000), "Себестоимость": Decimal(100000)}},
        type_names={},
        cost_type="Себестоимость",
        base_type="Розничная",
        window_days=14,
    )

    assert products[0].cost_kopecks == 100000


def test_product_without_base_price_is_skipped():
    products = assemble_products(
        nomenclatures=[{"Ref_Key": "n1", "Артикул": "A-1", "Наименование": "Товар"}],
        balances={},
        sales_rows=[],
        key_to_nom={},
        prices={},
        type_names={},
        cost_type="Себестоимость",
        base_type="Розничная",
        window_days=14,
    )
    assert products == []


def _base_available() -> bool:
    try:
        response = httpx.get(
            f"{ONEC_URL}/odata/standard.odata/$metadata",
            auth=("Администратор", ""),
            timeout=3,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _base_available(), reason="демо-база 1С недоступна")
def test_live_onec_source_smoke():
    source = OnecODataSource(ONEC_URL, user="Администратор", password="")
    products = source.fetch(as_of=date(2024, 3, 31), window_days=14)

    assert products
    assert all(product.base_price_kopecks > 0 for product in products)
    assert any(product.vendor_code == "NYM 1х4" for product in products)