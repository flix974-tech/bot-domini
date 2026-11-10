"""Client minimale per la Bot API di Telegram (solo stdlib: urllib)."""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.token = token

    def _call(self, method: str, payload: dict[str, Any], timeout: int = 15) -> Optional[dict]:
        url = _BASE.format(token=self.token, method=method)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            log.error("Chiamata Telegram %s fallita: %s", method, e)
            return None

    # --- invio messaggi ---
    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None,
                     parse_mode: Optional[str] = None) -> Optional[dict]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self._call("sendMessage", payload)

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> Optional[dict]:
        return self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True},
        )

    def answer_callback_query(self, cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
        payload: dict[str, Any] = {"callback_query_id": cb_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    # --- gestione webhook / polling ---
    def set_webhook(self, url: str, secret: Optional[str] = None) -> Optional[dict]:
        payload: dict[str, Any] = {"url": url, "allowed_updates": ["message", "callback_query"]}
        if secret:
            payload["secret_token"] = secret
        return self._call("setWebhook", payload)

    def delete_webhook(self) -> Optional[dict]:
        return self._call("deleteWebhook", {})

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        res = self._call("getUpdates", payload, timeout=timeout + 10)
        if res and res.get("ok"):
            return res.get("result", [])
        return []


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Costruisce un reply_markup inline da (testo, callback_data)."""
    return {
        "inline_keyboard": [
            [{"text": t, "callback_data": cb} for (t, cb) in row] for row in rows
        ]
    }
