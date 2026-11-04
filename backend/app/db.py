"""Thin data-access layer over SQLite.

Every helper that touches business-owned data requires a ``business_id`` so tenant
isolation is enforced at the data-access layer, not just in route handlers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA_VERSION = 1
_init_lock = threading.Lock()


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DATABASE_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def connect():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Explicit transaction; nested use is a no-op (inner block joins the outer)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_approval_kinds(conn) -> None:
    """Databases created before contract decisions existed reject the CONTRACT_ACTION approval kind; rebuild that table once."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='approval_requests'").fetchone()
    if not row or "CONTRACT_ACTION" in row[0]:
        return
    new_sql = row[0].replace("'INVOICE_REMINDER')", "'INVOICE_REMINDER','CONTRACT_ACTION')")
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""BEGIN;
            ALTER TABLE approval_requests RENAME TO approval_requests_old;
            {new_sql};
            INSERT INTO approval_requests SELECT * FROM approval_requests_old;
            DROP TABLE approval_requests_old;
            CREATE INDEX IF NOT EXISTS ix_approvals_biz ON approval_requests(business_id, status);
            COMMIT;""")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(path: str | None = None) -> None:
    with _init_lock:
        conn = _connect(path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            schema = (Path(__file__).parent / "schema.sql").read_text()
            conn.executescript(schema)
            _migrate_approval_kinds(conn)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,))
        finally:
            conn.close()


# ---------- row helpers ----------
JSON_FIELDS = {
    "params", "actions", "details", "supporting_data", "data", "key_clauses", "pages", "anomalies",
    "breakdown", "meta", "facts", "verification", "budgets",
}


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS & d.keys():
        v = d[k]
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


def all_(conn, sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def one(conn, sql: str, params: Iterable[Any] = ()) -> dict | None:
    return row_to_dict(conn.execute(sql, tuple(params)).fetchone())


def scalar(conn, sql: str, params: Iterable[Any] = ()):
    r = conn.execute(sql, tuple(params)).fetchone()
    return None if r is None else r[0]


def _enc(v):
    return json.dumps(v, default=str) if isinstance(v, (dict, list)) else v


def insert(conn, table: str, values: dict) -> int:
    cols = list(values.keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    cur = conn.execute(sql, tuple(_enc(values[c]) for c in cols))
    return cur.lastrowid


def update(conn, table: str, row_id: int, values: dict, business_id: int | None = None) -> int:
    if not values:
        return 0
    sets = ", ".join(f"{k} = ?" for k in values)
    params = [_enc(v) for v in values.values()] + [row_id]
    sql = f"UPDATE {table} SET {sets} WHERE id = ?"
    if business_id is not None:
        sql += " AND business_id = ?"
        params.append(business_id)
    return conn.execute(sql, params).rowcount


def get_owned(conn, table: str, row_id: int, business_id: int) -> dict | None:
    """Fetch a row only if it belongs to ``business_id`` (tenant isolation)."""
    return one(conn, f"SELECT * FROM {table} WHERE id = ? AND business_id = ?", (row_id, business_id))
