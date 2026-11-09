"""Database layer — SQLite with async support."""

import aiosqlite
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "proxies.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS proxies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT NOT NULL,
    port        INTEGER NOT NULL,
    protocol    TEXT DEFAULT 'http',
    anonymity   TEXT DEFAULT 'unknown',
    country     TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    latency_ms  REAL DEFAULT 0,
    is_alive    INTEGER DEFAULT 0,
    created_at  REAL DEFAULT 0,
    last_checked REAL DEFAULT 0,
    fail_count  INTEGER DEFAULT 0,
    UNIQUE(ip, port, protocol)
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    proxy       TEXT,
    target_ip   TEXT DEFAULT '',
    result      TEXT DEFAULT '',
    created_at  REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_proxies_alive ON proxies(is_alive);
CREATE INDEX IF NOT EXISTS idx_proxies_source ON proxies(source);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
"""


class DB:
    _instance = None
    _conn: aiosqlite.Connection | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def connect(self):
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(DB_PATH))
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(SCHEMA)
            await self._conn.commit()
        return self._conn

    async def get(self) -> aiosqlite.Connection:
        return await self.connect()

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None


db = DB()
