"""Файловый источник учётных данных."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from repricer.adapters.accounting import CsvAccountingSource, CsvSourceError


def write(tmp_path, products: str, sales: str):
    products_path = tmp_path / "products.csv"
    sales_path = tmp_path / "sales.csv"
    products_path.write_text(products, encoding="utf-8")
    sales_path.write_text(sales, encoding="utf-8")
    return CsvAccountingSource(products_path, sales_path)


def test_reads_products_and_window(tmp_path):
    source = write(
        tmp_path,
        "vendor_code,name,stock_qty,cost,base_price\nSKU-1,Товар,300,500.50,1500\n",
        "vendor_code,date,qty\n"
        "SKU-1,2024-03-31,10\n"
        "SKU-1,2024-03-30,4\n"
        "SKU-1,2024-03-01,1000\n",
    )
    products = source.fetch(as_of=date(2024, 3, 31), window_days=14)

    assert len(products) == 1
    product = products[0]
    assert product.vendor_code == "SKU-1"
    assert product.stock_qty == Decimal("300")
    assert product.cost_kopecks == 50050
    assert product.base_price_kopecks == 150000
    # Продажи до начала окна не попадают.
    assert sum(qty for _, qty in product.sales_by_day) == Decimal("14")


def test_sales_are_aggregated_and_fractional(tmp_path):
    source = write(
        tmp_path,
        "vendor_code,name,stock_qty,cost,base_price\nSKU-1,Товар,10,10,100\n",
        "vendor_code,date,qty\nSKU-1,2024-03-31,1\nSKU-1,2024-03-31,0.5\n",
    )
    products = source.fetch(as_of=date(2024, 3, 31), window_days=14)
    assert products[0].sales_by_day == ((date(2024, 3, 31), Decimal("1.5")),)


def test_duplicate_vendor_code_fails(tmp_path):
    source = write(
        tmp_path,
        "vendor_code,name,stock_qty,cost,base_price\nSKU-1,A,1,1,1\nSKU-1,B,1,1,1\n",
        "vendor_code,date,qty\n",
    )
    with pytest.raises(CsvSourceError):
        source.fetch(as_of=date(2024, 3, 31), window_days=14)


def test_missing_file_fails(tmp_path):
    source = CsvAccountingSource(tmp_path / "nope.csv", tmp_path / "nope2.csv")
    with pytest.raises(CsvSourceError):
        source.fetch(as_of=date(2024, 3, 31), window_days=14)
