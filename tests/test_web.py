"""Веб-интерфейс: таблица, очередь, сквозной цикл через формы."""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from emulator_wb.app.store import load_goods
from repricer.adapters.accounting import CsvAccountingSource
from repricer.adapters.marketplace import WBClient, WBClientError
from repricer.app import RepricerService
from repricer.storage import Repository
from repricer.web import create_app

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2024-03-31"


def build(tmp_path, start_emulator):
    goods = load_goods(ROOT / "emulator_wb" / "data" / "goods.json")
    url = start_emulator(goods)
    repo = Repository(tmp_path / "web.db")
    service = RepricerService(
        repo,
        CsvAccountingSource(ROOT / "data" / "products.csv", ROOT / "data" / "sales.csv"),
        WBClient(url, "token"),
        clock=lambda: datetime(2024, 3, 31, 12, 0, 0),
    )
    return TestClient(create_app(service, as_of_default=AS_OF, start_scheduler=False)), service, repo


def test_index_shows_zones_and_states(tmp_path, start_emulator):
    client, _service, _repo = build(tmp_path, start_emulator)

    response = client.get("/")

    assert response.status_code == 200
    assert "T-OUT" in response.text
    assert "аутсток" in response.text
    assert "оверсток" in response.text
    assert "нет в наличии" in response.text
    assert "нет на площадке" in response.text
    assert response.text.index("Базовая цена") < response.text.index("Текущая цена")
    assert "4\u00a0000,00 ₽" in response.text  # базовая цена T-DISC из CSV
    assert "маржа ниже минимальной" in response.text  # тег для T-WARN/T-WARNOUT
    assert "⚠" in response.text


def test_full_cycle_through_forms(tmp_path, start_emulator):
    client, _service, repo = build(tmp_path, start_emulator)

    client.post("/product/T-OUT/mode", data={"mode": "recommend"}, follow_redirects=False)
    run = client.post("/run", data={"as_of": AS_OF}, follow_redirects=False)
    assert run.status_code == 303

    queue = client.get("/queue")
    assert "T-OUT" in queue.text

    client.post(
        "/queue/decision", data={"action": "approve", "ids": "all"}, follow_redirects=False
    )
    send = client.post("/send", follow_redirects=False)
    assert send.status_code == 303

    for _ in range(300):
        client.post("/poll", follow_redirects=False)
        if repo.list_decisions(("applied",)):
            break
        time.sleep(0.01)

    applied = repo.list_decisions(("applied",))
    assert applied and applied[0]["vendor_code"] == "T-OUT"
    assert applied[0]["actual_price_kopecks"] == 153500  # 1490 × 1,03 → 1535 ₽ (коридор 1050–9000)

    product = client.get("/product/T-OUT")
    assert product.status_code == 200
    assert "применена" in product.text


def test_approve_all_selected_ids(tmp_path, start_emulator):
    client, service, repo = build(tmp_path, start_emulator)
    moment = datetime(2024, 3, 31, 12, 0, 0)
    repo.set_mode("T-OUT", "recommend", at=moment)
    repo.set_mode("T-OVER", "recommend", at=moment)
    client.post("/run", data={"as_of": AS_OF}, follow_redirects=False)

    queue = service.queue()
    assert len(queue) == 2
    ids = [str(row["id"]) for row in queue]

    client.post(
        "/queue/decision",
        data={"action": "approve", "ids": ids},
        follow_redirects=False,
    )

    assert len(repo.list_decisions(("approved",))) == 2
    assert service.queue() == []


def test_reject_hides_from_queue(tmp_path, start_emulator):
    client, _service, repo = build(tmp_path, start_emulator)
    repo.set_mode("T-OUT", "recommend", at=datetime(2024, 3, 31, 12, 0, 0))
    client.post("/run", data={"as_of": AS_OF}, follow_redirects=False)
    identifier = client.get("/queue").text
    assert "T-OUT" in identifier

    client.post(
        "/queue/decision", data={"action": "reject", "ids": "all"}, follow_redirects=False
    )

    assert "T-OUT" not in client.get("/queue").text
    assert repo.list_decisions(("rejected",))


def test_auto_decision_is_shown_and_rejectable_in_queue(tmp_path, start_emulator):
    client, _service, repo = build(tmp_path, start_emulator)
    repo.set_mode("T-OVER", "auto", at=datetime(2024, 3, 31, 12, 0, 0))
    client.post("/run", data={"as_of": AS_OF}, follow_redirects=False)

    page = client.get("/queue")
    assert "T-OVER" in page.text
    assert "авто" in page.text

    decision_id = repo.list_decisions(("auto",))[0]["id"]
    client.post(
        "/queue/decision",
        data={"action": "reject", "ids": str(decision_id)},
        follow_redirects=False,
    )

    assert [row["vendor_code"] for row in repo.list_decisions(("rejected",))] == ["T-OVER"]
    assert "T-OVER" not in client.get("/queue").text


def test_settings_page_updates_global_rules(tmp_path, start_emulator):
    client, service, _repo = build(tmp_path, start_emulator)

    assert client.get("/settings").status_code == 200
    client.post("/settings", data={"step_pct": "5"}, follow_redirects=False)

    assert service.global_rules().step_pct == Decimal("5")


def test_settings_auto_send_checkbox(tmp_path, start_emulator):
    client, service, _repo = build(tmp_path, start_emulator)

    client.post(
        "/settings",
        data={"sales_window_days": "14", "auto_send": "1"},
        follow_redirects=False,
    )
    assert service.auto_send_enabled() is True
    page = client.get("/settings")
    assert "Автоотправка" in page.text
    assert "checked" in page.text

    client.post(
        "/settings",
        data={"sales_window_days": "14"},
        follow_redirects=False,
    )
    assert service.auto_send_enabled() is False


def test_settings_sales_basis_switch(tmp_path, start_emulator):
    client, service, _repo = build(tmp_path, start_emulator)

    assert service.sales_basis() == "orders"
    client.post("/settings", data={"sales_basis": "buyouts"}, follow_redirects=False)

    assert service.sales_basis() == "buyouts"
    assert "по выкупам" in client.get("/settings").text


def test_settings_return_to_base_checkbox(tmp_path, start_emulator):
    client, service, _repo = build(tmp_path, start_emulator)

    assert service.global_rules().return_to_base is False
    client.post("/settings", data={"return_to_base": "1"}, follow_redirects=False)
    assert service.global_rules().return_to_base is True

    client.post("/settings", data={"sales_window_days": "14"}, follow_redirects=False)
    assert service.global_rules().return_to_base is False


def test_filters_by_zone(tmp_path, start_emulator):
    client, _service, _repo = build(tmp_path, start_emulator)

    response = client.get("/", params={"zone": "outage"})

    assert response.status_code == 200
    assert "T-OUT" in response.text
    assert "T-OVER" not in response.text


def test_bulk_mode_applies_only_to_displayed_rows(tmp_path, start_emulator):
    client, _service, repo = build(tmp_path, start_emulator)

    page = client.get("/", params={"zone": "outage"})
    assert 'action="/products/mode"' in page.text
    assert 'value="T-OUT"' in page.text
    assert 'value="T-OVER"' not in page.text

    client.post(
        "/products/mode",
        data={"mode": "auto", "codes": ["T-OUT", "T-CEIL"]},
        follow_redirects=False,
    )

    assert repo.get_product("T-OUT")["mode"] == "auto"
    assert repo.get_product("T-CEIL")["mode"] == "auto"
    assert repo.get_product("T-OVER")["mode"] == "off"


def test_bulk_mode_requires_mode(tmp_path, start_emulator):
    client, _service, repo = build(tmp_path, start_emulator)

    client.post("/products/mode", data={"codes": "T-OUT"}, follow_redirects=False)

    assert repo.get_product("T-OUT")["mode"] == "off"


def test_marketplace_error_is_shown_not_500(tmp_path, start_emulator):
    client, service, _repo = build(tmp_path, start_emulator)

    def boom():
        raise WBClientError("эмулятор недоступен")

    service.marketplace.list_goods = boom
    response = client.get("/")

    assert response.status_code == 200
    assert "Ошибка чтения данных" in response.text
    assert "эмулятор недоступен" in response.text
