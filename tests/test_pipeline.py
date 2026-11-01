"""Сквозной конвейер: CSV → расчёт → очередь → подтверждение → отправка → статус."""

from __future__ import annotations

import time
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
    "SKU-OUT,Товар аутсток,50,500,1500\n"
    "SKU-NORM,Товар норма,300,500,1500\n"
    "SKU-OVER,Товар оверсток,900,500,1500\n"
)
SALES = (
    "vendor_code,date,qty\n"
    "SKU-OUT,2024-03-31,140\n"
    "SKU-NORM,2024-03-31,140\n"
    "SKU-OVER,2024-03-31,140\n"
)


def build(tmp_path, start_emulator, goods, *, quarantine="30"):
    products_path = tmp_path / "products.csv"
    sales_path = tmp_path / "sales.csv"
    products_path.write_text(PRODUCTS, encoding="utf-8")
    sales_path.write_text(SALES, encoding="utf-8")

    url = start_emulator(goods, quarantine=quarantine)
    repo = Repository(tmp_path / "app.db")
    client = WBClient(url, "token")
    service = RepricerService(
        repo,
        CsvAccountingSource(products_path, sales_path),
        client,
        clock=lambda: AS_OF,
    )
    return service, client, repo


def default_goods():
    return [
        Good(1001, "SKU-OUT", 1490, 0),
        Good(1002, "SKU-NORM", 1500, 0),
        Good(1003, "SKU-OVER", 1500, 0),
    ]


def wait_decision(service, decision_id: int, *, status: str, attempts: int = 400):
    for _ in range(attempts):
        service.poll()
        row = service.repo.get_decision(decision_id)
        if row and row["status"] == status:
            return row
        time.sleep(0.01)
    raise AssertionError(f"решение не перешло в {status}")


def test_run_creates_only_actionable_recommendations(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-NORM", "recommend", at=AS_OF)
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)

    result = service.run(as_of=AS_OF)

    assert result["created"] == 1
    queue = service.queue()
    assert [row["vendor_code"] for row in queue] == ["SKU-OUT"]
    assert queue[0]["recommended_price_kopecks"] == 153500
    assert queue[0]["reason"] == "raise_outage"


def test_rerun_does_not_duplicate_recommendation(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)
    service.run(as_of=AS_OF)

    result = service.run(as_of=AS_OF)

    assert result["created"] == 0
    assert result["same_recommendation"] == 1
    assert len(service.queue()) == 1


def test_auto_rerun_does_not_duplicate(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "auto", at=AS_OF)
    first = service.run(as_of=AS_OF)
    second = service.run(as_of=AS_OF)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["same_recommendation"] == 1
    assert len(repo.list_decisions(("auto", "sent"))) == 1


def test_auto_is_visible_in_queue_and_can_be_rejected(tmp_path, start_emulator):
    service, client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "auto", at=AS_OF)
    service.run(as_of=AS_OF)

    assert [row["status"] for row in service.queue()] == ["auto"]

    assert service.reject() == 1
    assert repo.list_decisions(("auto",)) == []
    assert [row["vendor_code"] for row in repo.list_decisions(("rejected",))] == ["SKU-OUT"]

    assert service.send()["sent"] == 0
    market = {good.vendor_code: good for good in client.list_goods()}
    assert market["SKU-OUT"].discounted_price_kopecks == 149000


def test_approve_does_not_touch_auto(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "auto", at=AS_OF)
    service.run(as_of=AS_OF)

    assert service.approve() == 0
    assert [row["status"] for row in service.queue()] == ["auto"]


def test_approved_is_not_duplicated_before_send(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)
    service.run(as_of=AS_OF)
    service.approve()

    result = service.run(as_of=AS_OF)

    assert result["created"] == 0
    assert len(repo.list_decisions(("approved",))) == 1


def test_approve_send_and_apply(tmp_path, start_emulator):
    service, client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)
    service.run(as_of=AS_OF)
    decision_id = service.queue()[0]["id"]

    assert service.approve([decision_id]) == 1
    send = service.send()
    assert send["sent"] == 1
    wait_decision(service, decision_id, status="applied")

    row = repo.get_decision(decision_id)
    assert row["status"] == "applied"
    assert row["actual_price_kopecks"] == 153500
    goods = {good.vendor_code: good for good in client.list_goods()}
    assert goods["SKU-OUT"].discounted_price_kopecks == 153500


def test_pause_blocks_second_change(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)
    service.run(as_of=AS_OF)
    decision_id = service.queue()[0]["id"]
    service.approve([decision_id])
    service.send()
    wait_decision(service, decision_id, status="applied")

    result = service.run(as_of=AS_OF)
    row = next(item for item in service.table(as_of=AS_OF) if item["vendor_code"] == "SKU-OUT")

    assert result["created"] == 0
    assert row["reason"] == "pause"
    assert row["pending_id"] is None


def test_off_mode_never_queues(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "off", at=AS_OF)

    result = service.run(as_of=AS_OF)

    assert result["created"] == 0
    assert result["off"] == 2
    assert service.queue() == []


def test_auto_mode_sends_without_confirmation(tmp_path, start_emulator):
    service, client, repo = build(tmp_path, start_emulator, default_goods())
    repo.set_mode("SKU-OUT", "auto", at=AS_OF)
    service.run(as_of=AS_OF)

    send = service.send()
    assert send["sent"] == 1
    rows = repo.list_decisions(("auto", "sent"))
    assert len(rows) == 1
    wait_decision(service, rows[0]["id"], status="applied")

    goods = {good.vendor_code: good for good in client.list_goods()}
    assert goods["SKU-OUT"].discounted_price_kopecks == 153500


def test_discount_is_kept_and_effective_price_matches(tmp_path, start_emulator):
    goods = [Good(1001, "SKU-OUT", 1700, 10)]
    products = "vendor_code,name,stock_qty,cost,base_price\nSKU-OUT,Товар,50,500,1500\n"
    sales = "vendor_code,date,qty\nSKU-OUT,2024-03-31,140\n"
    products_path = tmp_path / "products.csv"
    sales_path = tmp_path / "sales.csv"
    products_path.write_text(products, encoding="utf-8")
    sales_path.write_text(sales, encoding="utf-8")

    url = start_emulator(goods)
    repo = Repository(tmp_path / "app.db")
    client = WBClient(url, "token")
    service = RepricerService(
        repo, CsvAccountingSource(products_path, sales_path), client, clock=lambda: AS_OF
    )
    repo.set_mode("SKU-OUT", "recommend", at=AS_OF)
    service.run(as_of=AS_OF)
    decision_id = service.queue()[0]["id"]
    service.approve([decision_id])
    service.send()
    row = wait_decision(service, decision_id, status="applied")

    assert row["sent_discount"] == 10
    assert row["sent_price_rubles"] == 1751  # 1751 × 0,9 = 1575,9 ₽, ближе всего к 1576 ₽
    assert row["actual_price_kopecks"] == 157590
    market = {good.vendor_code: good for good in client.list_goods()}
    assert market["SKU-OUT"].discounted_price_kopecks == 157590


def test_quarantine_is_shown(tmp_path, start_emulator):
    service, client, repo = build(tmp_path, start_emulator, default_goods(), quarantine="2")
    repo.set_mode("SKU-OVER", "auto", at=AS_OF)
    service.run(as_of=AS_OF)

    send = service.send()
    assert send["sent"] == 1
    decision_id = repo.list_decisions(("sent",))[0]["id"]
    wait_decision(service, decision_id, status="quarantine")

    row = repo.get_decision(decision_id)
    assert row["status"] == "quarantine"
    assert "арантин" in row["error_text"]
    market = {good.vendor_code: good for good in client.list_goods()}
    assert market["SKU-OVER"].discounted_price_kopecks == 150000


def test_table_reports_all_zones(tmp_path, start_emulator):
    service, _client, repo = build(tmp_path, start_emulator, default_goods())
    table = {row["vendor_code"]: row for row in service.table(as_of=AS_OF)}

    assert table["SKU-OUT"]["zone"] == "outage"
    assert table["SKU-NORM"]["zone"] == "normal"
    assert table["SKU-OVER"]["zone"] == "overstock"
    assert table["SKU-OVER"]["recommended_price_kopecks"] == 145500
    assert Decimal(table["SKU-NORM"]["days_of_stock"]) == Decimal("30.0")
