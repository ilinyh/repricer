"""Тесты эмулятора WB: контракт, асинхронность, ошибки, карантин."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from emulator_wb.app.main import create_app
from emulator_wb.app.store import Good, Store

AUTH = {"Authorization": "Bearer test-token"}


def make_client(*, delay: float = 0.0, quarantine: str = "30") -> TestClient:
    goods = [
        Good(1001, "SKU-1", 1000, 0),
        Good(1002, "SKU-2", 2000, 10),
        Good(1003, "SKU-3", 500, 0),
    ]
    store = Store(goods, apply_delay=delay, quarantine_pct=Decimal(quarantine))
    return TestClient(create_app(store))


@pytest.fixture
def client() -> TestClient:
    return make_client()


def upload(client: TestClient, items: list[dict]) -> dict:
    response = client.post("/api/v2/upload/task", json={"data": items}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["error"] is False
    return response.json()["data"]


def wait_task(client: TestClient, task_id: int) -> dict:
    for _ in range(400):
        response = client.get(
            "/api/v2/history/tasks", params={"uploadID": task_id}, headers=AUTH
        )
        assert response.status_code == 200
        data = response.json()["data"]
        if data["status"] == 3:
            return data
        time.sleep(0.005)
    raise AssertionError("задача не обработалась")


def goods_by_nm(client: TestClient) -> dict[int, dict]:
    response = client.get("/api/v2/list/goods/filter", headers=AUTH)
    assert response.status_code == 200
    return {item["nmID"]: item for item in response.json()["data"]["listGoods"]}


def test_auth_required(client: TestClient) -> None:
    response = client.get("/api/v2/list/goods/filter")
    assert response.status_code == 401
    body = response.json()
    assert body["error"] is True
    assert body["errorText"]


def test_list_shape_and_units(client: TestClient) -> None:
    goods = goods_by_nm(client)

    first = goods[1001]
    assert first["vendorCode"] == "SKU-1"
    assert first["discount"] == 0
    assert first["sizes"][0]["price"] == 100000
    assert first["sizes"][0]["discountedPrice"] == 100000

    second = goods[1002]
    assert second["sizes"][0]["price"] == 200000
    assert second["sizes"][0]["discountedPrice"] == 180000


def test_list_pagination_and_filters(client: TestClient) -> None:
    response = client.get(
        "/api/v2/list/goods/filter", params={"limit": 2, "offset": 2}, headers=AUTH
    )
    assert [item["nmID"] for item in response.json()["data"]["listGoods"]] == [1003]

    response = client.get(
        "/api/v2/list/goods/filter", params={"filterVendorCode": "SKU-2"}, headers=AUTH
    )
    assert [item["nmID"] for item in response.json()["data"]["listGoods"]] == [1002]

    response = client.get(
        "/api/v2/list/goods/filter", params={"filterNmID": 1001}, headers=AUTH
    )
    assert [item["nmID"] for item in response.json()["data"]["listGoods"]] == [1001]


def test_upload_applies_after_delay(client: TestClient) -> None:
    task = upload(client, [{"nmID": 1001, "price": 1100, "discount": 5}])
    assert task["alreadyExists"] is False

    data = wait_task(client, task["id"])
    assert data["goods"][0]["errorText"] == ""
    assert data["goods"][0]["price"] == 1100

    good = goods_by_nm(client)[1001]
    assert good["sizes"][0]["price"] == 110000
    assert good["sizes"][0]["discountedPrice"] == 104500  # 1100 × 0,95
    assert good["discount"] == 5


def test_batch_applies_all_items(client: TestClient) -> None:
    task = upload(
        client,
        [
            {"nmID": 1001, "price": 1200, "discount": 0},
            {"nmID": 1002, "price": 1900, "discount": 10},
        ],
    )
    data = wait_task(client, task["id"])

    assert len(data["goods"]) == 2
    assert all(item["errorText"] == "" for item in data["goods"])
    goods = goods_by_nm(client)
    assert goods[1001]["sizes"][0]["price"] == 120000
    assert goods[1002]["sizes"][0]["price"] == 190000


def test_per_item_errors_do_not_block_others(client: TestClient) -> None:
    task = upload(
        client,
        [
            {"nmID": 9999, "price": 1000, "discount": 0},
            {"nmID": 1001, "price": 1250, "discount": 0},
        ],
    )
    data = wait_task(client, task["id"])

    assert data["goods"][0]["errorText"]
    assert data["goods"][1]["errorText"] == ""
    assert goods_by_nm(client)[1001]["sizes"][0]["price"] == 125000

    detail = client.get(
        "/api/v2/history/goods/task", params={"uploadID": task["id"]}, headers=AUTH
    ).json()["data"]["goods"]
    assert detail[0]["errorCode"] == "unknown_nm"
    assert detail[0]["errorText"]


def test_invalid_price_and_discount(client: TestClient) -> None:
    task = upload(
        client,
        [
            {"nmID": 1001, "price": 0, "discount": 0},
            {"nmID": 1002, "price": 2000, "discount": 101},
        ],
    )
    data = wait_task(client, task["id"])

    codes = {
        item["nmID"]: item
        for item in client.get(
            "/api/v2/history/goods/task", params={"uploadID": task["id"]}, headers=AUTH
        ).json()["data"]["goods"]
    }
    assert codes[1001]["errorCode"] == "invalid_price"
    assert codes[1002]["errorCode"] == "invalid_discount"
    goods = goods_by_nm(client)
    assert goods[1001]["sizes"][0]["price"] == 100000
    assert goods[1002]["sizes"][0]["price"] == 200000


def test_quarantine_blocks_deep_drop(client: TestClient) -> None:
    task = upload(client, [{"nmID": 1001, "price": 600, "discount": 0}])
    wait_task(client, task["id"])

    detail = client.get(
        "/api/v2/history/goods/task", params={"uploadID": task["id"]}, headers=AUTH
    ).json()["data"]["goods"]
    assert detail[0]["errorCode"] == "quarantine"
    assert goods_by_nm(client)[1001]["sizes"][0]["price"] == 100000


def test_quarantine_boundary_is_strict() -> None:
    exact = make_client()
    task = upload(exact, [{"nmID": 1001, "price": 700, "discount": 0}])
    wait_task(exact, task["id"])
    assert goods_by_nm(exact)[1001]["sizes"][0]["price"] == 70000

    below = make_client()
    task = upload(below, [{"nmID": 1001, "price": 699, "discount": 0}])
    wait_task(below, task["id"])
    assert goods_by_nm(below)[1001]["sizes"][0]["price"] == 100000


def test_same_upload_while_processing_is_not_duplicated() -> None:
    client = make_client(delay=5)
    first = upload(client, [{"nmID": 1001, "price": 1100, "discount": 0}])
    second = upload(client, [{"nmID": 1001, "price": 1100, "discount": 0}])

    assert second["id"] == first["id"]
    assert second["alreadyExists"] is True

    status = client.get(
        "/api/v2/history/tasks", params={"uploadID": first["id"]}, headers=AUTH
    ).json()["data"]["status"]
    assert status == 1


def test_history_goods_pagination(client: TestClient) -> None:
    task = upload(
        client,
        [
            {"nmID": 1001, "price": 1100, "discount": 0},
            {"nmID": 1002, "price": 2100, "discount": 0},
            {"nmID": 1003, "price": 600, "discount": 0},
        ],
    )
    wait_task(client, task["id"])

    page = client.get(
        "/api/v2/history/goods/task",
        params={"uploadID": task["id"], "limit": 1, "offset": 1},
        headers=AUTH,
    ).json()["data"]["goods"]
    assert [item["nmID"] for item in page] == [1002]


def test_unknown_upload_id(client: TestClient) -> None:
    response = client.get("/api/v2/history/tasks", params={"uploadID": 999}, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"] is True


def test_rate_limit_returns_429_then_recovers() -> None:
    store = Store([Good(1001, "SKU-1", 1000, 0)])
    client = TestClient(create_app(store, rate_limit=1, rate_window=0.3))

    assert client.get("/api/v2/list/goods/filter", headers=AUTH).status_code == 200
    limited = client.get("/api/v2/list/goods/filter", headers=AUTH)
    assert limited.status_code == 429
    assert limited.json()["error"] is True
    assert "Retry-After" in limited.headers

    time.sleep(0.4)
    assert client.get("/api/v2/list/goods/filter", headers=AUTH).status_code == 200