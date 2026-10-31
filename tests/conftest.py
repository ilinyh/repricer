"""Общие фикстуры: запуск эмулятора WB в отдельном потоке на свободном порту."""

from __future__ import annotations

import socket
import threading
import time
from decimal import Decimal

import httpx
import pytest
import uvicorn

from emulator_wb.app.main import create_app
from emulator_wb.app.store import Good, Store


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # pragma: no cover - не нужны в тестах
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def start_emulator():
    """Фабрика: ``start_emulator(goods, delay=0, quarantine="30") -> base_url``."""

    servers: list[tuple[_Server, threading.Thread]] = []

    def _start(
        goods: list[Good], *, delay: float = 0.0, quarantine: str = "30"
    ) -> str:
        app = create_app(
            Store(goods, apply_delay=delay, quarantine_pct=Decimal(quarantine))
        )
        port = _free_port()
        server = _Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{port}"
        for _ in range(300):
            try:
                if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.02)
        else:  # pragma: no cover
            raise RuntimeError("эмулятор не поднялся")
        servers.append((server, thread))
        return url

    yield _start

    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)
