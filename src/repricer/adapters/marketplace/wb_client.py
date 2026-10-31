"""Клиент Wildberries «Цены и скидки».

Один и тот же код работает с эмулятором и с настоящим WB: меняются только
адрес и токен. Цены в списке — копейки, в загрузке — целые рубли.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

PROCESSING_STATUS = 1
PROCESSED_STATUS = 3


class WBClientError(RuntimeError):
    """Ошибка обращения к площадке."""


@dataclass(frozen=True)
class MarketGood:
    nm_id: int
    vendor_code: str
    price_kopecks: int
    discount: int
    discounted_price_kopecks: int


@dataclass(frozen=True)
class UploadPrice:
    nm_id: int
    price_rubles: int
    discount: int


@dataclass(frozen=True)
class TaskItemResult:
    nm_id: int
    vendor_code: str
    price_rubles: int
    discount: int
    error_code: str | None
    error_text: str


@dataclass(frozen=True)
class TaskStatus:
    upload_id: int
    status: int
    items: tuple[TaskItemResult, ...]

    @property
    def processing(self) -> bool:
        return self.status == PROCESSING_STATUS


class WBClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": token},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WBClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_goods(self) -> list[MarketGood]:
        goods: list[MarketGood] = []
        offset = 0
        limit = 1000
        while True:
            response = self._client.get(
                "/api/v2/list/goods/filter", params={"limit": limit, "offset": offset}
            )
            self._ensure_ok(response, "список товаров")
            payload = response.json()["data"]["listGoods"]
            goods.extend(self._parse_good(row) for row in payload)
            if len(payload) < limit:
                break
            offset += limit
        return goods

    def upload_task(self, items: list[UploadPrice]) -> tuple[int, bool]:
        if not items:
            raise WBClientError("Пустая пачка цен")
        body = {
            "data": [
                {"nmID": item.nm_id, "price": item.price_rubles, "discount": item.discount}
                for item in items
            ]
        }
        response = self._client.post("/api/v2/upload/task", json=body)
        self._ensure_ok(response, "загрузка цен")
        data = response.json()["data"]
        return int(data["id"]), bool(data.get("alreadyExists", False))

    def task_status(self, upload_id: int) -> TaskStatus:
        response = self._client.get(
            "/api/v2/history/tasks", params={"uploadID": upload_id}
        )
        self._ensure_ok(response, "статус задачи")
        data = response.json()["data"]
        items = tuple(
            TaskItemResult(
                nm_id=int(row["nmID"]),
                vendor_code=row.get("vendorCode", ""),
                price_rubles=int(row["price"]),
                discount=int(row["discount"]),
                error_code=(row.get("errorCode") or None),
                error_text=row.get("errorText", ""),
            )
            for row in data.get("goods", [])
        )
        return TaskStatus(
            upload_id=int(data["uploadID"]), status=int(data["status"]), items=items
        )

    @staticmethod
    def _parse_good(row: dict) -> MarketGood:
        size = (row.get("sizes") or [{}])[0]
        price = int(size.get("price", 0))
        discounted = int(size.get("discountedPrice", price))
        return MarketGood(
            nm_id=int(row["nmID"]),
            vendor_code=row.get("vendorCode", ""),
            price_kopecks=price,
            discount=int(row.get("discount", 0)),
            discounted_price_kopecks=discounted,
        )

    @staticmethod
    def _ensure_ok(response: httpx.Response, what: str) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("errorText") or response.text
        except ValueError:  # pragma: no cover
            detail = response.text
        raise WBClientError(f"{what}: HTTP {response.status_code}: {detail}")
