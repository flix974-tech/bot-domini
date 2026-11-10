"""Strato di accesso ai dati su Postgres (Vercel Postgres / Neon)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

import psycopg2
import psycopg2.extras

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   BIGINT PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TIMESTAMPTZ NOT NULL,
    decided_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS servers (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS domains (
    id            SERIAL PRIMARY KEY,
    server_id     INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    domain        TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_servers (
    user_id       BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    server_id     INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, server_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id            SERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(telegram_id),
    domain_id     INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    subdomain     TEXT NOT NULL,
    full_url      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    UNIQUE(domain_id, subdomain)
);
"""

# Crea lo schema una sola volta per container (cold start).
_schema_ready = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, cfg: Config) -> None:
        self.dsn = cfg.database_url

    @contextmanager
    def _conn(self) -> Iterator[psycopg2.extensions.connection]:
        conn = psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _cur(self):
        with self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                yield cur
            finally:
                cur.close()

    def ensure_schema(self) -> None:
        global _schema_ready
        if _schema_ready:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
        _schema_ready = True

    # ---------------- Utenti ----------------
    def upsert_pending_user(self, telegram_id: int, username: Optional[str], first_name: Optional[str]) -> str:
        with self._cur() as cur:
            cur.execute("SELECT status FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO users (telegram_id, username, first_name, status, created_at) "
                    "VALUES (%s, %s, %s, 'pending', %s)",
                    (telegram_id, username, first_name, _now()),
                )
                return "pending"
            cur.execute(
                "UPDATE users SET username = %s, first_name = %s WHERE telegram_id = %s",
                (username, first_name, telegram_id),
            )
            return row["status"]

    def ensure_user(self, telegram_id: int, username: Optional[str], first_name: Optional[str],
                    approved: bool = False) -> None:
        """Garantisce che l'utente esista in `users` (necessario per la FK di sessions).
        Se `approved`, lo porta a stato 'approved' (usato per il superadmin)."""
        with self._cur() as cur:
            cur.execute("SELECT status FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            if row is None:
                status = "approved" if approved else "pending"
                cur.execute(
                    "INSERT INTO users (telegram_id, username, first_name, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (telegram_id, username, first_name, status, _now()),
                )
            elif approved and row["status"] != "approved":
                cur.execute("UPDATE users SET status = 'approved' WHERE telegram_id = %s", (telegram_id,))

    def set_user_status(self, telegram_id: int, status: str) -> None:
        with self._cur() as cur:
            cur.execute(
                "UPDATE users SET status = %s, decided_at = %s WHERE telegram_id = %s",
                (status, _now(), telegram_id),
            )

    def get_user(self, telegram_id: int) -> Optional[dict]:
        with self._cur() as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            return cur.fetchone()

    def list_users(self, status: Optional[str] = None) -> list[dict]:
        with self._cur() as cur:
            if status:
                cur.execute("SELECT * FROM users WHERE status = %s ORDER BY created_at DESC", (status,))
            else:
                cur.execute("SELECT * FROM users ORDER BY created_at DESC")
            return cur.fetchall()

    def is_approved(self, telegram_id: int) -> bool:
        u = self.get_user(telegram_id)
        return bool(u and u["status"] == "approved")

    # ---------------- Permessi utente/server ----------------
    def grant_server(self, user_id: int, server_id: int) -> None:
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO user_servers (user_id, server_id, created_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, server_id) DO NOTHING",
                (user_id, server_id, _now()),
            )

    def revoke_server(self, user_id: int, server_id: int) -> int:
        with self._cur() as cur:
            cur.execute(
                "DELETE FROM user_servers WHERE user_id = %s AND server_id = %s",
                (user_id, server_id),
            )
            return cur.rowcount

    def list_user_servers(self, user_id: int) -> list[dict]:
        with self._cur() as cur:
            cur.execute(
                "SELECT s.* FROM user_servers us JOIN servers s ON s.id = us.server_id "
                "WHERE us.user_id = %s ORDER BY s.id",
                (user_id,),
            )
            return cur.fetchall()

    def user_allowed_server_ids(self, user_id: int) -> set[int]:
        with self._cur() as cur:
            cur.execute("SELECT server_id FROM user_servers WHERE user_id = %s", (user_id,))
            return {r["server_id"] for r in cur.fetchall()}

    # ---------------- Server ----------------
    def add_server(self, name: str) -> int:
        with self._cur() as cur:
            cur.execute("INSERT INTO servers (name, created_at) VALUES (%s, %s) RETURNING id", (name, _now()))
            return int(cur.fetchone()["id"])

    def list_servers(self) -> list[dict]:
        with self._cur() as cur:
            cur.execute("SELECT * FROM servers ORDER BY id")
            return cur.fetchall()

    def delete_server(self, server_id: int) -> int:
        with self._cur() as cur:
            cur.execute("DELETE FROM servers WHERE id = %s", (server_id,))
            return cur.rowcount

    # ---------------- Domini ----------------
    def add_domain(self, server_id: int, domain: str) -> int:
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO domains (server_id, domain, created_at) VALUES (%s, %s, %s) RETURNING id",
                (server_id, domain, _now()),
            )
            return int(cur.fetchone()["id"])

    def list_domains(self, server_id: Optional[int] = None) -> list[dict]:
        with self._cur() as cur:
            if server_id is not None:
                cur.execute(
                    "SELECT d.*, s.name AS server_name FROM domains d JOIN servers s ON s.id = d.server_id "
                    "WHERE d.server_id = %s ORDER BY d.id",
                    (server_id,),
                )
            else:
                cur.execute(
                    "SELECT d.*, s.name AS server_name FROM domains d JOIN servers s ON s.id = d.server_id "
                    "ORDER BY d.id"
                )
            return cur.fetchall()

    def delete_domain(self, domain_id: int) -> int:
        with self._cur() as cur:
            cur.execute("DELETE FROM domains WHERE id = %s", (domain_id,))
            return cur.rowcount

    def subdomain_exists(self, domain_id: int, subdomain: str) -> bool:
        with self._cur() as cur:
            cur.execute(
                "SELECT 1 FROM sessions WHERE domain_id = %s AND subdomain = %s",
                (domain_id, subdomain),
            )
            return cur.fetchone() is not None

    # ---------------- Sessioni ----------------
    def add_session(self, user_id: int, domain_id: int, subdomain: str, full_url: str) -> int:
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO sessions (user_id, domain_id, subdomain, full_url, created_at) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (user_id, domain_id, subdomain, full_url, _now()),
            )
            return int(cur.fetchone()["id"])

    def list_sessions(self, user_id: Optional[int] = None, limit: int = 50) -> list[dict]:
        with self._cur() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT * FROM sessions WHERE user_id = %s ORDER BY id DESC LIMIT %s",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT s.*, u.username FROM sessions s JOIN users u ON u.telegram_id = s.user_id "
                    "ORDER BY s.id DESC LIMIT %s",
                    (limit,),
                )
            return cur.fetchall()
