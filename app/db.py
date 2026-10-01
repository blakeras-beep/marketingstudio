"""Database: Postgres in production (DATABASE_URL), SQLite for local dev and tests.

One small adapter so the SQL is written once: queries use `?` placeholders and are
translated for psycopg. Keep the SQL portable (no JSONB, no RETURNING-only tricks
SQLite lacks; SQLite >= 3.35 supports RETURNING).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id {serial} PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'marketing', 'csm')),
        password_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        created_by INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    # Marketing inputs: facts the BDX feed and Blueprint don't carry. Current value per
    # (scope, subject, field); every change is also appended to input_history.
    """CREATE TABLE IF NOT EXISTS inputs (
        scope TEXT NOT NULL,          -- community | plan | home
        subject TEXT NOT NULL,        -- community: SubdivisionNumber; plan: <sub>|<plan name>; home: SpecNumber
        field TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_by INTEGER,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (scope, subject, field)
    )""",
    """CREATE TABLE IF NOT EXISTS input_history (
        id {serial} PRIMARY KEY,
        scope TEXT NOT NULL,
        subject TEXT NOT NULL,
        field TEXT NOT NULL,
        value TEXT,
        updated_by INTEGER,
        updated_at TEXT NOT NULL
    )""",
]

_is_pg = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()
_sqlite: sqlite3.Connection | None = None


def is_postgres() -> bool:
    return _is_pg


def _sqlite_conn() -> sqlite3.Connection:
    global _sqlite
    if _sqlite is None:
        path = DATABASE_URL[len("sqlite:///"):] if DATABASE_URL.startswith("sqlite:///") else ":memory:"
        _sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _sqlite.row_factory = sqlite3.Row
    return _sqlite


class Conn:
    """Thin wrapper: q(sql, params) -> list[dict]; one(...) -> dict | None; x(...) -> rowcount."""

    def __init__(self, raw, pg: bool):
        self.raw, self.pg = raw, pg

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def q(self, sql: str, params=()) -> list[dict]:
        cur = self.raw.execute(self._sql(sql), params)
        if cur.description is None:
            return []
        return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql: str, params=()) -> int:
        return self.raw.execute(self._sql(sql), params).rowcount


@contextmanager
def connect():
    """A connection inside one transaction (committed on success, rolled back on error)."""
    if _is_pg:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as raw:  # commits on clean exit
            yield Conn(raw, True)
    else:
        with _lock:
            raw = _sqlite_conn()
            raw.execute("BEGIN")
            try:
                yield Conn(raw, False)
                raw.execute("COMMIT")
            except Exception:
                raw.execute("ROLLBACK")
                raise


def init() -> None:
    serial = "SERIAL" if _is_pg else "INTEGER"
    with connect() as c:
        for stmt in SCHEMA:
            c.x(stmt.format(serial=serial))


def reset_for_tests() -> None:
    """Fresh in-memory SQLite (tests only)."""
    global _sqlite
    assert not _is_pg, "never reset a Postgres database"
    _sqlite = None
    init()
