"""Runner locale in long-polling: utile per sviluppo/test senza Vercel.

Usa lo stesso database (Postgres) e la stessa logica del webhook.
Avvio:  python -m bot.main
"""
from __future__ import annotations

import logging

from .config import load_config
from .db import Database
from .logic import process_update
from .telegram_api import TelegramAPI


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("bot")

    cfg = load_config()
    db = Database(cfg)
    db.ensure_schema()
    api = TelegramAPI(cfg.bot_token)

    # Polling e webhook sono mutuamente esclusivi: rimuovi l'eventuale webhook.
    api.delete_webhook()
    log.info("Bot in polling. Superadmin ID: %s", cfg.superadmin_id)

    offset = None
    while True:
        updates = api.get_updates(offset=offset, timeout=25)
        for u in updates:
            offset = u["update_id"] + 1
            try:
                process_update(u, api, db, cfg)
            except Exception as e:  # noqa: BLE001
                log.error("Errore update: %r", e)


if __name__ == "__main__":
    main()
