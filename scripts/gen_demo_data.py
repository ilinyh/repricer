"""Генератор демо-данных: согласованные CSV учёта и товары эмулятора WB.

Товары подобраны так, чтобы покрыть все зоны, оба ограничителя коридора,
блокировку по марже, предупреждение о марже, отсутствие остатка и продаж,
ровно граничные 14 и 60 дней, мелкий шаг и скидку площадки.
Один товар намеренно отсутствует на площадке.
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EMULATOR_SEED = ROOT / "emulator_wb" / "data" / "goods.json"

AS_OF = date(2024, 3, 31)
WINDOW_DAYS = 14

# code, name, stock, cost, base_price, market_price_before, discount, sold_last14, on_market
PRODUCTS: list[tuple[str, str, int, str, str, int | None, int, int, bool]] = [
    ("T-NORM", "Футболка базовая", 300, "500", "1500", 1500, 0, 140, True),
    ("T-OUT", "Куртка зимняя", 50, "500", "1500", 1490, 0, 140, True),
    ("T-OVER", "Термос 1 л", 900, "500", "1500", 1333, 0, 140, True),
    ("T-CORRIDOR", "Набор посуды", 900, "500", "1500", 1060, 0, 140, True),
    ("T-MARGIN", "Сумка кожаная", 900, "1150", "1500", 1380, 0, 140, True),
    ("T-WARN", "Шарф шерстяной", 300, "1300", "1500", 1400, 0, 140, True),
    ("T-NOSALES", "Пылесос", 40, "500", "1500", 1500, 0, 0, True),
    ("T-ZERO", "Лампа настольная", 0, "500", "1500", 1500, 0, 140, True),
    ("T-CEIL", "Кофемашина", 50, "500", "1500", 8900, 0, 140, True),
    ("T-ATCEIL", "Чайник электрический", 50, "500", "1500", 9000, 0, 140, True),
    ("T-WARNOUT", "Рюкзак городской", 50, "1300", "1500", 1410, 0, 140, True),
    ("T-EXACT14", "Кружка керамическая", 140, "500", "1500", 1500, 0, 140, True),
    ("T-EXACT60", "Вешалка-плечики", 600, "500", "1500", 1500, 0, 140, True),
    ("T-OVER60", "Зонт автомат", 601, "500", "1500", 1500, 0, 140, True),
    ("T-DISC", "Кроссовки беговые", 50, "1500", "4000", 3800, 10, 140, True),
    ("T-CHEAP", "Носки хлопковые", 1, "1", "10", 10, 0, 14, True),
    ("T-MISSING", "Товар без карточки", 100, "100", "1000", None, 0, 140, False),
]

# Возвраты за окно (для демонстрации переключателя «заказы / выкупы»).
RETURNS_TOTAL = {"T-NORM": 84}


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with (DATA_DIR / "products.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["vendor_code", "name", "stock_qty", "cost", "base_price"])
        for code, name, stock, cost, base, _price, _disc, _sold, _on_market in PRODUCTS:
            writer.writerow([code, name, stock, cost, base])

    with (DATA_DIR / "sales.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["vendor_code", "date", "qty", "returns"])
        for code, _name, _stock, _cost, _base, _price, _disc, sold, _on_market in PRODUCTS:
            if sold <= 0:
                continue
            per_day, remainder = divmod(sold, WINDOW_DAYS)
            returns_total = RETURNS_TOTAL.get(code, 0)
            returns_per_day, returns_remainder = divmod(returns_total, WINDOW_DAYS)
            for offset in range(WINDOW_DAYS):
                qty = per_day + (remainder if offset == WINDOW_DAYS - 1 else 0)
                if qty <= 0:
                    continue
                returns_qty = returns_per_day + (
                    returns_remainder if offset == WINDOW_DAYS - 1 else 0
                )
                day = AS_OF - timedelta(days=WINDOW_DAYS - 1 - offset)
                writer.writerow([code, day.isoformat(), qty, returns_qty])

    goods = []
    nm_id = 100001
    for code, _name, _stock, _cost, _base, price, discount, _sold, on_market in PRODUCTS:
        if not on_market or price is None:
            continue
        goods.append({"nmID": nm_id, "vendorCode": code, "price": price, "discount": discount})
        nm_id += 1

    EMULATOR_SEED.parent.mkdir(parents=True, exist_ok=True)
    EMULATOR_SEED.write_text(
        json.dumps({"goods": goods}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"products: {len(PRODUCTS)}")
    print(f"goods: {len(goods)}")
    print(f"as_of: {AS_OF.isoformat()}")


if __name__ == "__main__":
    main()
