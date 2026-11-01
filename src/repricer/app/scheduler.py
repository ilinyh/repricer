"""Планировщик пересчёта: прогон и обновление статусов по расписанию."""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

DEFAULT_CHECK_SECONDS = 30.0
SCHEDULE_SETTING = "schedule_minutes"


class Scheduler:
    """Простой планировщик в отдельном потоке.

    Интервал в минутах читается из настроек (0 — выключен) на каждой итерации,
    поэтому его можно менять из интерфейса без перезапуска.
    """

    def __init__(self, service, *, poll: bool = True, check_seconds: float = DEFAULT_CHECK_SECONDS) -> None:
        self.service = service
        self.poll = poll
        self.check_seconds = check_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._tick_event = threading.Event()

    # --- настройки ------------------------------------------------------
    def interval_minutes(self) -> int:
        raw = self.service.repo.get_setting(
            SCHEDULE_SETTING, os.getenv("REPRICER_SCHEDULE_MINUTES", "0")
        )
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    # --- работа ---------------------------------------------------------
    def tick(self, *, trigger: str = "schedule") -> dict | None:
        """Один прогон и обновление статусов. Пропускается, если уже идёт."""

        if not self._lock.acquire(blocking=False):
            log.info("Прогон уже выполняется, пропускаю")
            return None
        try:
            result = self.service.run(as_of=self.service.clock(), trigger=trigger)
            if self.service.auto_send_enabled():
                # То же, что кнопка «Отправить одобренные»: одобренные и авто.
                self.service.send()
            if self.poll:
                self.service.poll()
            return result
        except Exception:  # noqa: BLE001 - планировщик не должен падать
            log.exception("Ошибка планового прогона")
            return None
        finally:
            self._lock.release()

    def run_now(self) -> dict | None:
        """Внеочередной прогон (например, по кнопке)."""

        result = self.tick(trigger="manual")
        self._tick_event.set()
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            minutes = self.interval_minutes()
            if minutes <= 0:
                self._stop.wait(self.check_seconds)
                continue
            # Ждём интервал, но раньше просыпаемся по run_now/stop.
            self._tick_event.wait(minutes * 60)
            self._tick_event.clear()
            if self._stop.is_set():
                break
            if self.interval_minutes() > 0:
                self.tick()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="repricer-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._tick_event.set()
        if self._thread:
            self._thread.join(timeout=5)
