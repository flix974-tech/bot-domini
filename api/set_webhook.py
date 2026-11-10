"""Utility one-shot: apri nel browser /api/set_webhook per registrare il webhook.

Legge l'URL pubblico del deploy da VERCEL_URL (iniettata da Vercel) oppure dal
parametro ?url=... e chiama setWebhook su Telegram. Protetta: richiede ?key=<SUPERADMIN_ID>.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import load_config  # noqa: E402
from bot.telegram_api import TelegramAPI  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        cfg = load_config()
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)

        # Protezione minima: ?key=<uno dei tuoi SUPERADMIN_ID>
        key = params.get("key", [""])[0]
        if not key.isdigit() or int(key) not in cfg.superadmin_ids:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden: aggiungi ?key=<SUPERADMIN_ID>")
            return

        base = params.get("url", [""])[0].strip()
        if not base:
            host = os.getenv("VERCEL_URL", "").strip()
            if host:
                base = f"https://{host}"
        if not base:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"manca l'URL: usa ?url=https://tuo-progetto.vercel.app")
            return

        webhook_url = base.rstrip("/") + "/api/webhook"
        api = TelegramAPI(cfg.bot_token)
        res = api.set_webhook(webhook_url, secret=cfg.webhook_secret or None)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        ok = bool(res and res.get("ok"))
        msg = f"setWebhook -> {webhook_url}\nEsito: {'OK ✅' if ok else 'FALLITO'}\nRisposta: {res}"
        self.wfile.write(msg.encode("utf-8"))
