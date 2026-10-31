"""Сгенерировать товары эмулятора WB из живой базы 1С.

Берём артикулы и базовую цену (вид цены «Розничная») из 1С, ставим цену до скидки
равной базовой, скидку 0. nmID присваиваются последовательно.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from repricer.adapters.accounting.onec_odata import OnecODataSource  # noqa: E402

BASE_URL = os.getenv("ONEC_URL", "http://localhost:8081/ut115")
USER = os.getenv("ONEC_USER", "Администратор")
PASSWORD = os.getenv("ONEC_PASSWORD", "")
AS_OF = date.fromisoformat(os.getenv("ONEC_AS_OF", "2024-03-31"))
OUT = Path(os.getenv("ONEC_GOODS_OUT", ROOT / "emulator_wb" / "data" / "goods_1c.json"))


def main() -> None:
    source = OnecODataSource(BASE_URL, user=USER, password=PASSWORD)
    products = source.fetch(as_of=AS_OF, window_days=14)

    goods = []
    for index, product in enumerate(products, start=1):
        price = max(1, int(round(product.base_price_kopecks / 100)))
        goods.append(
            {
                "nmID": 200000 + index,
                "vendorCode": product.vendor_code,
                "price": price,
                "discount": 0,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"goods": goods}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{datetime.now().isoformat(timespec='seconds')}: {len(goods)} товаров -> {OUT}")


if __name__ == "__main__":
    main()