"""Хранилище приложения на SQLite (стандартная библиотека, без ORM)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    vendor_code TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    nm_id       INTEGER,
    mode        TEXT NOT NULL DEFAULT 'off',
    overrides   TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    as_of       TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    status      TEXT NOT NULL,
    products    INTEGER NOT NULL DEFAULT 0,
    changed     INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL DEFAULT 0,
    superseded  INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS decisions (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                     INTEGER,
    vendor_code                TEXT NOT NULL,
    name                       TEXT NOT NULL DEFAULT '',
    nm_id                      INTEGER,
    as_of                      TEXT NOT NULL,
    zone                       TEXT NOT NULL,
    sales_per_day              TEXT NOT NULL,
    days_of_stock              TEXT,
    current_price_kopecks      INTEGER NOT NULL,
    recommended_price_kopecks  INTEGER NOT NULL,
    base_price_kopecks         INTEGER NOT NULL DEFAULT 0,
    cost_kopecks               INTEGER NOT NULL DEFAULT 0,
    margin_before              TEXT,
    margin_after               TEXT,
    reason                     TEXT NOT NULL,
    message                    TEXT NOT NULL DEFAULT '',
    limiters                   TEXT NOT NULL DEFAULT '[]',
    margin_warning             INTEGER NOT NULL DEFAULT 0,
    status                     TEXT NOT NULL,
    created_at                 TEXT NOT NULL,
    resolved_at                TEXT,
    upload_id                  INTEGER,
    sent_price_rubles          INTEGER,
    sent_discount              INTEGER,
    actual_price_kopecks       INTEGER,
    error_code                 TEXT,
    error_text                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
CREATE INDEX IF NOT EXISTS idx_decisions_vendor ON decisions(vendor_code, status);
CREATE INDEX IF NOT EXISTS idx_decisions_upload ON decisions(upload_id, nm_id);

CREATE TABLE IF NOT EXISTS tasks (
    upload_id  INTEGER PRIMARY KEY,
    status     INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    vendor_code TEXT,
    event       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}'
);
"""


def _iso(value: datetime) -> str:
    return value.isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - защита от старых записей
        return None


class Repository:
    """Доступ к данным. Один объект на приложение, операции под блокировкой."""

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- служебное ------------------------------------------------------
    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)))

    # --- настройки ------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- товары ---------------------------------------------------------
    def upsert_product(
        self,
        vendor_code: str,
        name: str,
        nm_id: int | None,
        *,
        at: datetime,
    ) -> None:
        self._execute(
            "INSERT INTO products (vendor_code, name, nm_id, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vendor_code) DO UPDATE SET "
            "name = excluded.name, nm_id = COALESCE(excluded.nm_id, products.nm_id), "
            "updated_at = excluded.updated_at",
            (vendor_code, name, nm_id, _iso(at)),
        )

    def list_products(self) -> dict[str, dict]:
        rows = self._query("SELECT * FROM products")
        return {
            row["vendor_code"]: {
                "name": row["name"],
                "nm_id": row["nm_id"],
                "mode": row["mode"],
                "overrides": json.loads(row["overrides"] or "{}"),
            }
            for row in rows
        }

    def get_product(self, vendor_code: str) -> dict:
        return self.list_products().get(
            vendor_code, {"name": "", "nm_id": None, "mode": "off", "overrides": {}}
        )

    def set_mode(self, vendor_code: str, mode: str, *, at: datetime) -> None:
        self.upsert_product(vendor_code, self.get_product(vendor_code)["name"], None, at=at)
        self._execute(
            "UPDATE products SET mode = ?, updated_at = ? WHERE vendor_code = ?",
            (mode, _iso(at), vendor_code),
        )

    def set_overrides(self, vendor_code: str, overrides: dict, *, at: datetime) -> None:
        self.upsert_product(vendor_code, self.get_product(vendor_code)["name"], None, at=at)
        self._execute(
            "UPDATE products SET overrides = ?, updated_at = ? WHERE vendor_code = ?",
            (json.dumps(overrides, ensure_ascii=False), _iso(at), vendor_code),
        )

    # --- прогоны --------------------------------------------------------
    def create_run(self, *, as_of: datetime, trigger: str, started_at: datetime) -> int:
        cursor = self._execute(
            "INSERT INTO runs (started_at, as_of, trigger, status) VALUES (?, ?, ?, 'running')",
            (_iso(started_at), _iso(as_of), trigger),
        )
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, finished_at: datetime, counters: dict, message: str = "") -> None:
        self._execute(
            "UPDATE runs SET finished_at = ?, status = ?, products = ?, changed = ?, "
            "created = ?, superseded = ?, message = ? WHERE id = ?",
            (
                _iso(finished_at),
                status,
                int(counters.get("products", 0)),
                int(counters.get("changed", 0)),
                int(counters.get("created", 0)),
                int(counters.get("superseded", 0)),
                message,
                run_id,
            ),
        )

    def last_run(self) -> dict | None:
        rows = self._query("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        return dict(rows[0]) if rows else None

    # --- решения --------------------------------------------------------
    def create_decision(self, **fields: Any) -> int:
        fields.setdefault("limiters", "[]")
        fields.setdefault("margin_warning", 0)
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cursor = self._execute(
            f"INSERT INTO decisions ({columns}) VALUES ({marks})", list(fields.values())
        )
        return int(cursor.lastrowid)

    def get_decision(self, decision_id: int) -> dict | None:
        rows = self._query("SELECT * FROM decisions WHERE id = ?", (decision_id,))
        return dict(rows[0]) if rows else None

    def list_decisions(self, statuses: Iterable[str]) -> list[dict]:
        statuses = tuple(statuses)
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = self._query(
            f"SELECT * FROM decisions WHERE status IN ({marks}) ORDER BY id", statuses
        )
        return [dict(row) for row in rows]

    def pending_for(self, vendor_code: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM decisions WHERE vendor_code = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (vendor_code,),
        )
        return dict(rows[0]) if rows else None

    def decisions_by_upload(self, upload_id: int, statuses: Iterable[str]) -> list[dict]:
        statuses = tuple(statuses)
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = self._query(
            f"SELECT * FROM decisions WHERE upload_id = ? AND status IN ({marks}) "
            "ORDER BY id",
            (upload_id, *statuses),
        )
        return [dict(row) for row in rows]

    def update_decision(self, decision_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._execute(
            f"UPDATE decisions SET {assignments} WHERE id = ?",
            (*fields.values(), decision_id),
        )

    def supersede_pending(self, vendor_code: str, *, at: datetime) -> int:
        cursor = self._execute(
            "UPDATE decisions SET status = 'superseded', resolved_at = ? "
            "WHERE vendor_code = ? AND status = 'pending'",
            (_iso(at), vendor_code),
        )
        return cursor.rowcount

    def last_applied_at(self, vendor_code: str, *, before: datetime | None = None) -> datetime | None:
        if before is None:
            rows = self._query(
                "SELECT MAX(resolved_at) AS at FROM decisions "
                "WHERE vendor_code = ? AND status = 'applied'",
                (vendor_code,),
            )
        else:
            rows = self._query(
                "SELECT MAX(resolved_at) AS at FROM decisions "
                "WHERE vendor_code = ? AND status = 'applied' AND resolved_at <= ?",
                (vendor_code, _iso(before)),
            )
        return parse_dt(rows[0]["at"]) if rows else None

    # --- задачи площадки ------------------------------------------------
    def save_task(self, upload_id: int, status: int, *, at: datetime) -> None:
        self._execute(
            "INSERT INTO tasks (upload_id, status, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(upload_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
            (upload_id, status, _iso(at), _iso(at)),
        )

    def set_task_status(self, upload_id: int, status: int, *, at: datetime) -> None:
        self._execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE upload_id = ?",
            (status, _iso(at), upload_id),
        )

    def tasks_with_status(self, status: int) -> list[dict]:
        rows = self._query("SELECT * FROM tasks WHERE status = ? ORDER BY upload_id", (status,))
        return [dict(row) for row in rows]

    # --- журнал ---------------------------------------------------------
    def append_journal(
        self,
        event: str,
        *,
        at: datetime,
        vendor_code: str | None = None,
        payload: dict | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO journal (created_at, vendor_code, event, payload) VALUES (?, ?, ?, ?)",
            (_iso(at), vendor_code, event, json.dumps(payload or {}, ensure_ascii=False)),
        )

    def journal(self, *, vendor_code: str | None = None, limit: int = 200) -> list[dict]:
        if vendor_code:
            rows = self._query(
                "SELECT * FROM journal WHERE vendor_code = ? ORDER BY id DESC LIMIT ?",
                (vendor_code, limit),
            )
        else:
            rows = self._query("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "vendor_code": row["vendor_code"],
                "event": row["event"],
                "payload": json.loads(row["payload"] or "{}"),
            }
            for row in rows
        ]

    def last_journal_event(self, vendor_code: str, event: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM journal WHERE vendor_code = ? AND event = ? ORDER BY id DESC LIMIT 1",
            (vendor_code, event),
        )
        if not rows:
            return None
        row = rows[0]
        return {"event": row["event"], "payload": json.loads(row["payload"] or "{}")}
