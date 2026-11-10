"""Caricamento e validazione della configurazione da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    superadmin_id: int
    db_path: str
    link_scheme: str
    subdomain_length: int


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise SystemExit("ERRORE: BOT_TOKEN non impostato (vedi .env.example)")

    raw_admin = os.getenv("SUPERADMIN_ID", "").strip()
    if not raw_admin.isdigit():
        raise SystemExit("ERRORE: SUPERADMIN_ID deve essere il tuo user ID Telegram numerico")

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
        superadmin_id=int(raw_admin),
        db_path=os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db",
        link_scheme=scheme,
        subdomain_length=sub_len,
    )
