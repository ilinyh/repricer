"""Клиент площадки против эмулятора."""

from __future__ import annotations

import time

import httpx
import pytest

from emulator_wb.app.store import Good
from repricer.adapters.marketplace import UploadPrice, WBClient, WBClientError


def wait(client: WBClient, upload_id: int, *, attempts: int = 300):
    for _ in range(attempts):
        status = client.task_status(upload_id)
        if not status.processing:
            return status
        time.sleep(0.01)
    raise AssertionError("задача не обработалась")


def test_list_units_and_effective_price(start_emulator):
    url = start_emulator(
        [Good(1001, "SKU-1", 1000, 0), Good(1002, "SKU-2", 2000, 10)]
    )
    with WBClient(url, "test-token") as client:
        goods = {good.vendor_code: good for good in client.list_goods()}

    assert goods["SKU-1"].price_kopecks == 100000
    assert goods["SKU-1"].discounted_price_kopecks == 100000
    assert goods["SKU-2"].price_kopecks == 200000
    assert goods["SKU-2"].discounted_price_kopecks == 180000


def test_upload_and_status(start_emulator):
    url = start_emulator([Good(1001, "SKU-1", 1000, 0)])
    with WBClient(url, "test-token") as client:
        upload_id, already = client.upload_task([UploadPrice(1001, 1100, 5)])
        assert already is False
        status = wait(client, upload_id)
        assert status.items[0].error_text == ""
        assert status.items[0].error_code is None
        goods = {good.vendor_code: good for good in client.list_goods()}

    assert goods["SKU-1"].discounted_price_kopecks == 104500


def test_error_codes_are_exposed(start_emulator):
    url = start_emulator([Good(1001, "SKU-1", 1000, 0)])
    with WBClient(url, "test-token") as client:
        upload_id, _ = client.upload_task(
            [UploadPrice(9999, 1000, 0), UploadPrice(1001, 600, 0)]
        )
        status = wait(client, upload_id)

    by_nm = {item.nm_id: item for item in status.items}
    assert by_nm[9999].error_code == "unknown_nm"
    assert by_nm[1001].error_code == "quarantine"


def test_client_retries_on_429():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                json={"error": True, "errorText": "limit"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "listGoods": [
                        {
                            "nmID": 1001,
                            "vendorCode": "SKU-1",
                            "discount": 0,
                            "sizes": [{"price": 100000, "discountedPrice": 100000}],
                        }
                    ]
                }
            },
        )

    client = WBClient(
        "http://test", "token", transport=httpx.MockTransport(handler), retries=2, backoff=0
    )
    with client:
        goods = client.list_goods()

    assert calls["count"] == 2
    assert goods[0].vendor_code == "SKU-1"


def test_client_raises_after_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": True, "errorText": "limit"}, headers={"Retry-After": "0"}
        )

    client = WBClient(
        "http://test", "token", transport=httpx.MockTransport(handler), retries=1, backoff=0
    )
    with client:
        with pytest.raises(WBClientError):
            client.list_goods()
