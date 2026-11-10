"""Strato di accesso ai dati (SQLite, stdlib)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | banned
    created_at    TEXT NOT NULL,
    decided_at    TEXT
);

CREATE TABLE IF NOT EXISTS servers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domains (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id     INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    domain        TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_servers (
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    server_id     INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, server_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    domain_id     INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    subdomain     TEXT NOT NULL,
    full_url      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(domain_id, subdomain)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ---------------- Utenti ----------------
    def upsert_pending_user(self, telegram_id: int, username: Optional[str], first_name: Optional[str]) -> str:
        """Registra l'utente se nuovo. Ritorna lo stato corrente."""
        with self._conn() as c:
            row = c.execute("SELECT status FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO users (telegram_id, username, first_name, status, created_at) "
                    "VALUES (?, ?, ?, 'pending', ?)",
                    (telegram_id, username, first_name, _now()),
                )
                return "pending"
            # aggiorna i metadati ma non lo stato
            c.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                (username, first_name, telegram_id),
            )
            return row["status"]

    def set_user_status(self, telegram_id: int, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE users SET status = ?, decided_at = ? WHERE telegram_id = ?",
                (status, _now(), telegram_id),
            )

    def get_user(self, telegram_id: int) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

    def list_users(self, status: Optional[str] = None) -> list[sqlite3.Row]:
        with self._conn() as c:
            if status:
                return c.execute(
                    "SELECT * FROM users WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            return c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    def is_approved(self, telegram_id: int) -> bool:
        u = self.get_user(telegram_id)
        return bool(u and u["status"] == "approved")

    # ---------------- Server ----------------
    def add_server(self, name: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO servers (name, created_at) VALUES (?, ?)", (name, _now())
            )
            return int(cur.lastrowid)

    def list_servers(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM servers ORDER BY id").fetchall()

    def delete_server(self, server_id: int) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            return cur.rowcount

    # ---------------- Permessi utente/server ----------------
    def grant_server(self, user_id: int, server_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO user_servers (user_id, server_id, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, server_id, _now()),
            )

    def revoke_server(self, user_id: int, server_id: int) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM user_servers WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
            return cur.rowcount

    def list_user_servers(self, user_id: int) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT s.* FROM user_servers us "
                "JOIN servers s ON s.id = us.server_id WHERE us.user_id = ? ORDER BY s.id",
                (user_id,),
            ).fetchall()

    def user_allowed_server_ids(self, user_id: int) -> set[int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT server_id FROM user_servers WHERE user_id = ?", (user_id,)
            ).fetchall()
            return {r["server_id"] for r in rows}

    # ---------------- Domini ----------------
    def add_domain(self, server_id: int, domain: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO domains (server_id, domain, created_at) VALUES (?, ?, ?)",
                (server_id, domain, _now()),
            )
            return int(cur.lastrowid)

    def list_domains(self, server_id: Optional[int] = None) -> list[sqlite3.Row]:
        with self._conn() as c:
            if server_id is not None:
                return c.execute(
                    "SELECT d.*, s.name AS server_name FROM domains d "
                    "JOIN servers s ON s.id = d.server_id WHERE d.server_id = ? ORDER BY d.id",
                    (server_id,),
                ).fetchall()
            return c.execute(
                "SELECT d.*, s.name AS server_name FROM domains d "
                "JOIN servers s ON s.id = d.server_id ORDER BY d.id"
            ).fetchall()

    def delete_domain(self, domain_id: int) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
            return cur.rowcount

    def subdomain_exists(self, domain_id: int, subdomain: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM sessions WHERE domain_id = ? AND subdomain = ?",
                (domain_id, subdomain),
            ).fetchone()
            return row is not None

    # ---------------- Sessioni ----------------
    def add_session(self, user_id: int, domain_id: int, subdomain: str, full_url: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO sessions (user_id, domain_id, subdomain, full_url, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, domain_id, subdomain, full_url, _now()),
            )
            return int(cur.lastrowid)

    def list_sessions(self, user_id: Optional[int] = None, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as c:
            if user_id is not None:
                return c.execute(
                    "SELECT * FROM sessions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return c.execute(
                "SELECT s.*, u.username FROM sessions s "
                "JOIN users u ON u.telegram_id = s.user_id ORDER BY s.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
