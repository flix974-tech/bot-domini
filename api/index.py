"""Entrypoint unico per Vercel (nuovo runtime Python).

Un solo handler che smista le richieste in base al percorso:
  POST /api/webhook       -> riceve gli update di Telegram
  GET  /api/set_webhook   -> registra il webhook (apri una volta nel browser)
  GET  /                  -> health check
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

# Rende importabile il pacchetto `bot` dalla radice del progetto.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import load_config  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.logic import process_update  # noqa: E402
from bot.telegram_api import TelegramAPI  # noqa: E402


class handler(BaseHTTPRequestHandler):
    # --- util ---
    def _send(self, code: int, body: str = "ok") -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path.rstrip("/")

    # --- routing ---
    def do_GET(self) -> None:
        if self._path().endswith("/api/set_webhook"):
            self._set_webhook()
        else:
            self._send(200, "Bot attivo")

    def do_POST(self) -> None:
        if self._path().endswith("/api/webhook"):
            self._handle_webhook()
        else:
            self._send(404, "not found")

    # --- POST /api/webhook ---
    def _handle_webhook(self) -> None:
        try:
            cfg = load_config()
        except Exception as e:  # noqa: BLE001
            print("Config non valida:", e)
            self._send(200, "config error")
            return

        if cfg.webhook_secret:
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.webhook_secret:
                self._send(401, "unauthorized")
                return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            update = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            self._send(400, "bad json")
            return

        try:
            db = Database(cfg)
            db.ensure_schema()
            process_update(update, TelegramAPI(cfg.bot_token), db, cfg)
        except Exception as e:  # noqa: BLE001
            print("Errore gestione update:", repr(e))

        self._send(200, "ok")

    # --- GET /api/set_webhook ---
    def _set_webhook(self) -> None:
        try:
            cfg = load_config()
        except Exception as e:  # noqa: BLE001
            self._send(200, f"config error: {e}")
            return

        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        key = params.get("key", [""])[0]
        if not key.isdigit() or int(key) not in cfg.superadmin_ids:
            self._send(403, "forbidden: aggiungi ?key=<SUPERADMIN_ID>")
            return

        # Base URL: ?url=..., altrimenti il dominio con cui stai aprendo la pagina.
        base = params.get("url", [""])[0].strip()
        if not base:
            host = self.headers.get("Host", "").strip()
            if host:
                base = f"https://{host}"
        if not base:
            self._send(400, "manca l'URL: usa ?url=https://tuo-progetto.vercel.app")
            return

        webhook_url = base.rstrip("/") + "/api/webhook"
        res = TelegramAPI(cfg.bot_token).set_webhook(webhook_url, secret=cfg.webhook_secret or None)
        ok = bool(res and res.get("ok"))
        self._send(200, f"setWebhook -> {webhook_url}\nEsito: {'OK ✅' if ok else 'FALLITO'}\nRisposta: {res}")
