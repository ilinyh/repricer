"""Переключатель «по заказам / по выкупам» (п. 6 ТЗ)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from emulator_wb.app.store import Good
from repricer.adapters.accounting import CsvAccountingSource
from repricer.adapters.marketplace import WBClient
from repricer.app import RepricerService
from repricer.storage import Repository

AS_OF = datetime(2024, 3, 31, 12, 0, 0)

PRODUCTS = (
    "vendor_code,name,stock_qty,cost,base_price\n"
    "SKU-NORM,Товар,300,500,1500\n"
)
# 140 заказов и 100 возвратов за 14 дней: по заказам — 10/день (норма),
# по выкупам — 40/14 ≈ 2,86/день (оверсток).
SALES = (
    "vendor_code,date,qty,returns\n"
    "SKU-NORM,2024-03-31,140,100\n"
)


def build(tmp_path, start_emulator):
    products_path = tmp_path / "products.csv"
    sales_path = tmp_path / "sales.csv"
    products_path.write_text(PRODUCTS, encoding="utf-8")
    sales_path.write_text(SALES, encoding="utf-8")
    url = start_emulator([Good(1001, "SKU-NORM", 1500, 0)])
    service = RepricerService(
        Repository(tmp_path / "app.db"),
        CsvAccountingSource(products_path, sales_path),
        WBClient(url, "token"),
        clock=lambda: AS_OF,
    )
    return service


def test_basis_switches_demand_between_orders_and_buyouts(tmp_path, start_emulator):
    service = build(tmp_path, start_emulator)

    assert service.sales_basis() == "orders"
    row = service.table(as_of=AS_OF)[0]
    assert row["zone"] == "normal"
    assert row["sales_per_day"] == Decimal("10")
    assert row["days_of_stock"] == Decimal("30.0")

    service.set_sales_basis("buyouts")
    row = service.table(as_of=AS_OF)[0]
    assert row["zone"] == "overstock"
    assert row["sales_per_day"].quantize(Decimal("0.01")) == Decimal("2.86")
    assert row["recommended_price_kopecks"] == 145500


def test_basis_round_trip_and_invalid_value(tmp_path, start_emulator):
    service = build(tmp_path, start_emulator)

    service.set_sales_basis("buyouts")
    assert service.sales_basis() == "buyouts"
    service.set_sales_basis("unknown")
    assert service.sales_basis() == "orders"