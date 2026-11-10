"""Caricamento e validazione della configurazione da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv assente in produzione (Vercel) va bene
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    superadmin_ids: tuple[int, ...]
    database_url: str
    link_scheme: str
    subdomain_length: int
    webhook_secret: str

    @property
    def superadmin_id(self) -> int:
        """Primo superadmin (compatibilità)."""
        return self.superadmin_ids[0]

    def is_admin(self, uid: int) -> bool:
        return uid in self.superadmin_ids


def _first_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return ""


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN non impostato")

    # Uno o più superadmin: ID separati da virgola (o spazio).
    # Accetta sia SUPERADMIN_ID che SUPERADMIN_IDS.
    raw_admin = _first_env("SUPERADMIN_ID", "SUPERADMIN_IDS")
    admin_ids = tuple(
        int(tok) for tok in raw_admin.replace(",", " ").split() if tok.isdigit()
    )
    if not admin_ids:
        raise RuntimeError("SUPERADMIN_ID deve contenere almeno un user ID Telegram numerico")

    # Vercel Postgres inietta POSTGRES_URL; accettiamo anche DATABASE_URL.
    database_url = _first_env("POSTGRES_URL", "DATABASE_URL", "POSTGRES_PRISMA_URL")
    if not database_url:
        raise RuntimeError("Nessuna connessione Postgres (POSTGRES_URL / DATABASE_URL)")

    scheme = os.getenv("LINK_SCHEME", "http").strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"

    try:
        sub_len = int(os.getenv("SUBDOMAIN_LENGTH", "10"))
    except ValueError:
        sub_len = 10
    sub_len = max(4, min(sub_len, 32))

    return Config(
        bot_token=bot_token,
        superadmin_ids=admin_ids,
        database_url=database_url,
        link_scheme=scheme,
        subdomain_length=sub_len,
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
    )
