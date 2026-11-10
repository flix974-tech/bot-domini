"""Endpoint serverless per Vercel: riceve gli update di Telegram via webhook.

Telegram chiama POST https://<progetto>.vercel.app/api/webhook ad ogni messaggio.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Rende importabile il pacchetto `bot` dalla radice del progetto.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import load_config  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.logic import process_update  # noqa: E402
from bot.telegram_api import TelegramAPI  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _respond(self, code: int = 200, body: str = "ok") -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self) -> None:  # health check
        self._respond(200, "Bot attivo")

    def do_POST(self) -> None:
        try:
            cfg = load_config()
        except Exception as e:  # noqa: BLE001
            print("Config non valida:", e)
            self._respond(200, "config error")
            return

        # Verifica del secret del webhook (se impostato)
        if cfg.webhook_secret:
            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if got != cfg.webhook_secret:
                self._respond(401, "unauthorized")
                return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            update = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            self._respond(400, "bad json")
            return

        try:
            db = Database(cfg)
            db.ensure_schema()
            api = TelegramAPI(cfg.bot_token)
            process_update(update, api, db, cfg)
        except Exception as e:  # noqa: BLE001
            # Rispondiamo 200 per evitare che Telegram ritenti all'infinito.
            print("Errore gestione update:", repr(e))

        self._respond(200, "ok")
