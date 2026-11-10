"""Entry point del bot."""
from __future__ import annotations

import logging

from telegram.ext import Application

from .config import load_config
from .db import Database
from .handlers import setup_handlers


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    db = Database(cfg.db_path)

    app = Application.builder().token(cfg.bot_token).build()
    setup_handlers(app, cfg, db)

    logging.getLogger(__name__).info("Bot avviato. Superadmin ID: %s", cfg.superadmin_id)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
