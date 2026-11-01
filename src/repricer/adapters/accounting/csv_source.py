"""Файловый импорт: две CSV-таблицы — товары и продажи по дням.

products.csv: vendor_code,name,stock_qty,cost,base_price
sales.csv:    vendor_code,date,qty

Цены в файле — в рублях с копейками, внутри — целые копейки.
На этом источнике проект запускается без доступа к учётной системе.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .base import AccountingProduct


class CsvSourceError(RuntimeError):
    """Ошибка чтения файлового источника."""


def _to_kopecks(value: str, *, where: str) -> int:
    text = (value or "").strip().replace(",", ".")
    if not text:
        raise CsvSourceError(f"{where}: пустое значение цены")
    try:
        rubles = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - защита от мусора в файле
        raise CsvSourceError(f"{where}: не число: {value!r}") from exc
    return int((rubles * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _to_decimal(value: str, *, where: str) -> Decimal:
    text = (value or "").strip().replace(",", ".")
    if not text:
        raise CsvSourceError(f"{where}: пустое значение")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover
        raise CsvSourceError(f"{where}: не число: {value!r}") from exc


@dataclass(frozen=True)
class CsvAccountingSource:
    """Источник учётных данных из CSV."""

    products_path: Path
    sales_path: Path

    def fetch(self, *, as_of: date, window_days: int) -> list[AccountingProduct]:
        products = self._read_products()
        sales, returns = self._read_sales(as_of=as_of, window_days=window_days)

        result: list[AccountingProduct] = []
        for vendor_code, row in products.items():
            result.append(
                AccountingProduct(
                    vendor_code=vendor_code,
                    name=row["name"],
                    stock_qty=row["stock_qty"],
                    cost_kopecks=row["cost_kopecks"],
                    base_price_kopecks=row["base_price_kopecks"],
                    sales_by_day=tuple(sorted(sales.get(vendor_code, {}).items())),
                    incoming_qty=row["incoming_qty"],
                    incoming_date=row["incoming_date"],
                    returns_by_day=tuple(sorted(returns.get(vendor_code, {}).items())),
                )
            )
        result.sort(key=lambda product: product.vendor_code)
        return result

    def _read_products(self) -> dict[str, dict]:
        path = Path(self.products_path)
        if not path.exists():
            raise CsvSourceError(f"файл товаров не найден: {path}")
        products: dict[str, dict] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_no, row in enumerate(csv.DictReader(handle), start=2):
                code = (row.get("vendor_code") or "").strip()
                if not code:
                    raise CsvSourceError(f"{path}:{line_no}: пустой vendor_code")
                if code in products:
                    raise CsvSourceError(f"{path}:{line_no}: дубликат артикула {code!r}")
                raw_incoming_date = (row.get("incoming_date") or "").strip()
                incoming_date = None
                if raw_incoming_date:
                    try:
                        incoming_date = datetime.strptime(raw_incoming_date, "%Y-%m-%d").date()
                    except ValueError as exc:
                        raise CsvSourceError(
                            f"{path}:{line_no}: дата поставки не распознана: {raw_incoming_date!r}"
                        ) from exc
                raw_incoming_qty = (row.get("incoming_qty") or "").strip()
                products[code] = {
                    "name": (row.get("name") or "").strip(),
                    "stock_qty": _to_decimal(row.get("stock_qty", ""), where=f"{path}:{line_no}"),
                    "cost_kopecks": _to_kopecks(row.get("cost", ""), where=f"{path}:{line_no}"),
                    "base_price_kopecks": _to_kopecks(
                        row.get("base_price", ""), where=f"{path}:{line_no}"
                    ),
                    "incoming_qty": (
                        _to_decimal(raw_incoming_qty, where=f"{path}:{line_no}")
                        if raw_incoming_qty
                        else Decimal(0)
                    ),
                    "incoming_date": incoming_date,
                }
        return products

    def _read_sales(
        self, *, as_of: date, window_days: int
    ) -> tuple[dict[str, dict[date, Decimal]], dict[str, dict[date, Decimal]]]:
        path = Path(self.sales_path)
        if not path.exists():
            raise CsvSourceError(f"файл продаж не найден: {path}")
        start = as_of - timedelta(days=window_days - 1)
        sales: dict[str, dict[date, Decimal]] = {}
        returns: dict[str, dict[date, Decimal]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_no, row in enumerate(csv.DictReader(handle), start=2):
                code = (row.get("vendor_code") or "").strip()
                raw_date = (row.get("date") or "").strip()
                if not code or not raw_date:
                    continue
                try:
                    day = datetime.strptime(raw_date, "%Y-%m-%d").date()
                except ValueError as exc:
                    raise CsvSourceError(f"{path}:{line_no}: дата не распознана: {raw_date!r}") from exc
                if not (start <= day <= as_of):
                    continue
                qty = _to_decimal(row.get("qty", ""), where=f"{path}:{line_no}")
                bucket = sales.setdefault(code, {})
                bucket[day] = bucket.get(day, Decimal(0)) + qty
                raw_returns = (row.get("returns") or "").strip()
                if raw_returns:
                    value = _to_decimal(raw_returns, where=f"{path}:{line_no}")
                    if value:
                        returns.setdefault(code, {})
                        returns[code][day] = returns[code].get(day, Decimal(0)) + value
        return sales, returns
