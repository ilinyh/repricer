"""Планировщик: прогон по тику, идемпотентность, настройка интервала."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from emulator_wb.app.store import load_goods
from repricer.adapters.accounting import CsvAccountingSource
from repricer.adapters.marketplace import WBClient
from repricer.app import RepricerService, Scheduler
from repricer.storage import Repository

ROOT = Path(__file__).resolve().parent.parent
AS_OF = datetime(2024, 3, 31, 12, 0, 0)


def build(tmp_path, start_emulator):
    url = start_emulator(load_goods(ROOT / "emulator_wb" / "data" / "goods.json"))
    repo = Repository(tmp_path / "scheduler.db")
    service = RepricerService(
        repo,
        CsvAccountingSource(ROOT / "data" / "products.csv", ROOT / "data" / "sales.csv"),
        WBClient(url, "token"),
        clock=lambda: AS_OF,
    )
    return service, repo


def test_tick_creates_then_does_not_duplicate(tmp_path, start_emulator):
    service, repo = build(tmp_path, start_emulator)
    repo.set_mode("T-OUT", "recommend", at=AS_OF)
    scheduler = Scheduler(service)

    first = scheduler.tick()
    second = scheduler.tick()

    assert first is not None and first["created"] == 1
    assert second is not None and second["created"] == 0
    assert len(service.queue()) == 1


def test_schedule_minutes_round_trip(tmp_path, start_emulator):
    service, _repo = build(tmp_path, start_emulator)

    assert service.schedule_minutes() == 0
    service.set_schedule_minutes(15)
    assert service.schedule_minutes() == 15
    service.set_schedule_minutes(-5)
    assert service.schedule_minutes() == 0


def test_start_stop_is_safe_when_disabled(tmp_path, start_emulator):
    service, _repo = build(tmp_path, start_emulator)
    scheduler = Scheduler(service, check_seconds=0.05)

    scheduler.start()
    scheduler.stop()

    assert scheduler.interval_minutes() == 0


def test_auto_send_setting_round_trip(tmp_path, start_emulator):
    service, _repo = build(tmp_path, start_emulator)

    assert service.auto_send_enabled() is False
    service.set_auto_send(True)
    assert service.auto_send_enabled() is True
    service.set_auto_send(False)
    assert service.auto_send_enabled() is False


def test_auto_send_sends_approved_and_auto(tmp_path, start_emulator):
    service, repo = build(tmp_path, start_emulator)
    repo.set_mode("T-DISC", "auto", at=AS_OF)
    repo.set_mode("T-OUT", "recommend", at=AS_OF)
    repo.set_mode("T-OVER", "recommend", at=AS_OF)
    scheduler = Scheduler(service)

    scheduler.tick()
    service.approve()
    service.set_auto_send(True)
    scheduler.tick()

    # Автоотправка делает то же, что кнопка: одобренные и авто.
    sent = repo.list_decisions(("sent", "applied"))
    assert sorted(row["vendor_code"] for row in sent) == ["T-DISC", "T-OUT", "T-OVER"]
    assert repo.list_decisions(("approved",)) == []
    assert repo.list_decisions(("pending",)) == []


def test_auto_send_disabled_sends_nothing(tmp_path, start_emulator):
    service, repo = build(tmp_path, start_emulator)
    repo.set_mode("T-DISC", "auto", at=AS_OF)
    scheduler = Scheduler(service)

    scheduler.tick()

    assert repo.list_decisions(("auto",))
    assert repo.list_decisions(("sent",)) == []
    assert repo.tasks_with_status(1) == []
