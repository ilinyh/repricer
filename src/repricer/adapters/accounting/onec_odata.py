"""Источник учётных данных из 1С:УТ через стандартный интерфейс OData.

Сопоставление с ТЗ:
- товары — справочник «Номенклатура» (поле «Артикул»);
- остатки — регистр накопления «ТоварыНаСкладах», виртуальная таблица Balance;
- продажи и себестоимость продаж — «ВыручкаИСебестоимостьПродаж» (Количество, Стоимость);
- цены — регистр сведений «ЦеныНоменклатуры», срез последних, вид цены «Розничная»;
- себестоимость — вид цены «Себестоимость», при отсутствии — из продаж (Стоимость / Количество).

Имена объектов и полей заданы константами и при необходимости переопределяются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import httpx

from .base import AccountingProduct

log = logging.getLogger(__name__)

ODATA_PATH = "/odata/standard.odata"
MAX_ROWS = 100_000
VALUE_SAFE = "'(),$"


@dataclass(frozen=True)
class OnecNames:
    nomenclature: str = "Catalog_Номенклатура"
    balances: str = "AccumulationRegister_ТоварыНаСкладах/Balance"
    sales: str = "AccumulationRegister_ВыручкаИСебестоимостьПродаж_RecordType"
    analytics_keys: str = "Catalog_КлючиАналитикиУчетаНоменклатуры"
    prices: str = "InformationRegister_ЦеныНоменклатуры_RecordType/SliceLast"
    price_types: str = "Catalog_ВидыЦен"


class OnecODataSource:
    """Живой источник учёта. Новый адаптер, ядро не меняется."""

    def __init__(
        self,
        base_url: str,
        *,
        user: str | None = None,
        password: str = "",
        cost_price_type: str = "Себестоимость",
        base_price_type: str = "Розничная",
        names: OnecNames = OnecNames(),
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (user, password) if user is not None else None
        self._transport = transport
        self._timeout = timeout
        self._names = names
        self._cost_type = cost_price_type
        self._base_type = base_price_type

    # --- HTTP -----------------------------------------------------------
    def _get(self, entity: str, params: dict[str, str]) -> list[dict]:
        # Кодируем вручную: 1С OData не принимает "+" как пробел, а кавычки в
        # литералах datetime'...' должны остаться как есть.
        query = "&".join(
            f"{quote(key, safe='$')}={quote(str(value), safe=VALUE_SAFE)}"
            for key, value in params.items()
        )
        url = f"{self._base}{ODATA_PATH}/{entity}?{query}"
        with httpx.Client(
            auth=self._auth, transport=self._transport, timeout=self._timeout
        ) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json().get("value", [])

    def _get_all(self, entity: str, params: dict[str, str]) -> list[dict]:
        params = {"$format": "json", "$top": str(MAX_ROWS), **params}
        return self._get(entity, params)

    # --- данные ---------------------------------------------------------
    def fetch(self, *, as_of: date, window_days: int) -> list[AccountingProduct]:
        nomenclatures = self._get_all(
            self._names.nomenclature,
            {
                "$select": "Ref_Key,Артикул,Description",
                "$filter": "IsFolder eq false and DeletionMark eq false",
            },
        )
        balances = self._balances(as_of)
        sales_rows, key_to_nom = self._sales(as_of, window_days)
        prices, type_names = self._prices(as_of)

        return assemble_products(
            nomenclatures=nomenclatures,
            balances=balances,
            sales_rows=sales_rows,
            key_to_nom=key_to_nom,
            prices=prices,
            type_names=type_names,
            cost_type=self._cost_type,
            base_type=self._base_type,
            window_days=window_days,
        )

    def _balances(self, as_of: date) -> dict[str, Decimal]:
        rows = self._get_all(
            self._names.balances,
            {"Period": f"datetime'{as_of.isoformat()}T00:00:00'"},
        )
        balances: dict[str, Decimal] = {}
        for row in rows:
            ref = row.get("Номенклатура_Key")
            if not ref:
                continue
            value = Decimal(str(row.get("ВНаличииBalance") or 0))
            balances[ref] = balances.get(ref, Decimal(0)) + value
        return balances

    def _sales(self, as_of: date, window_days: int) -> tuple[list[dict], dict[str, str]]:
        start = as_of - timedelta(days=window_days - 1)
        rows = self._get_all(
            self._names.sales,
            {
                "$select": "АналитикаУчетаНоменклатуры_Key,Period,Количество,Стоимость",
                "$filter": (
                    f"Period ge datetime'{start.isoformat()}T00:00:00' and "
                    f"Period le datetime'{as_of.isoformat()}T23:59:59'"
                ),
            },
        )
        keys = self._get_all(
            self._names.analytics_keys, {"$select": "Ref_Key,Номенклатура_Key"}
        )
        return rows, {row["Ref_Key"]: row.get("Номенклатура_Key") for row in keys}

    def _prices(self, as_of: date) -> tuple[dict[str, dict[str, Decimal]], dict[str, str]]:
        rows = self._get_all(
            self._names.prices,
            {
                "$select": "Номенклатура_Key,ВидЦены_Key,Цена",
                "Period": f"datetime'{as_of.isoformat()}T00:00:00'",
            },
        )
        type_rows = self._get_all(
            self._names.price_types, {"$select": "Ref_Key,Description"}
        )
        type_names = {row["Ref_Key"]: row.get("Description", "") for row in type_rows}
        prices: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            nom = row.get("Номенклатура_Key")
            type_name = type_names.get(row.get("ВидЦены_Key", ""), "")
            if not nom or not type_name:
                continue
            # В 1С цена в рублях, внутри — копейки.
            value = (Decimal(str(row.get("Цена") or 0)) * 100).to_integral_value(
                rounding=ROUND_HALF_UP
            )
            prices.setdefault(nom, {})[type_name] = Decimal(value)
        return prices, type_names


def assemble_products(
    *,
    nomenclatures: list[dict],
    balances: dict[str, Decimal],
    sales_rows: list[dict],
    key_to_nom: dict[str, str],
    prices: dict[str, dict[str, Decimal]],
    type_names: dict[str, str],
    cost_type: str,
    base_type: str,
    window_days: int,
) -> list[AccountingProduct]:
    """Собрать товары из уже прочитанных строк OData (чистая функция)."""

    sales_by_nom: dict[str, dict[date, Decimal]] = {}
    qty_sum: dict[str, Decimal] = {}
    cost_sum: dict[str, Decimal] = {}
    for row in sales_rows:
        nom = key_to_nom.get(row.get("АналитикаУчетаНоменклатуры_Key", ""))
        if not nom:
            continue
        raw_period = row.get("Period")
        if not raw_period:
            continue
        try:
            day = datetime.fromisoformat(raw_period).date()
        except ValueError:
            continue
        qty = Decimal(str(row.get("Количество") or 0))
        sales_by_nom.setdefault(nom, {})[day] = sales_by_nom.setdefault(nom, {}).get(day, Decimal(0)) + qty
        qty_sum[nom] = qty_sum.get(nom, Decimal(0)) + qty
        cost_sum[nom] = cost_sum.get(nom, Decimal(0)) + Decimal(str(row.get("Стоимость") or 0))

    result: list[AccountingProduct] = []
    seen: set[str] = set()
    for row in nomenclatures:
        code = (row.get("Артикул") or "").strip()
        ref = row.get("Ref_Key")
        if not code or not ref:
            continue
        if code in seen:
            log.warning("Дубликат артикула %s в 1С — оставляю первый", code)
            continue

        price_row = prices.get(ref, {})
        base_kopecks = price_row.get(base_type, Decimal(0))
        if base_kopecks <= 0:
            continue

        cost_kopecks = price_row.get(cost_type, Decimal(0))
        if cost_kopecks <= 0 and qty_sum.get(ref, Decimal(0)) > 0:
            cost_kopecks = (cost_sum[ref] * 100 / qty_sum[ref]).to_integral_value(
                rounding=ROUND_HALF_UP
            )

        seen.add(code)
        result.append(
            AccountingProduct(
                vendor_code=code,
                name=(row.get("Description") or row.get("Наименование") or "").strip(),
                stock_qty=balances.get(ref, Decimal(0)),
                cost_kopecks=int(cost_kopecks),
                base_price_kopecks=int(base_kopecks),
                sales_by_day=tuple(sorted(sales_by_nom.get(ref, {}).items())),
            )
        )

    result.sort(key=lambda product: product.vendor_code)
    return result