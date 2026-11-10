"""Funzioni di supporto: generazione sottodomini, link, escaping."""
from __future__ import annotations

import secrets
import string

# Niente caratteri ambigui, solo minuscole + cifre: sottodomini DNS validi.
_ALPHABET = string.ascii_lowercase + string.digits


def random_subdomain(length: int = 10) -> str:
    # Primo carattere una lettera (alcuni resolver rifiutano label che iniziano con cifra).
    first = secrets.choice(string.ascii_lowercase)
    rest = "".join(secrets.choice(_ALPHABET) for _ in range(max(0, length - 1)))
    return first + rest


def build_url(scheme: str, subdomain: str, domain: str) -> str:
    return f"{scheme}://{subdomain}.{domain}"


def escape_md(text: str | None) -> str:
    """Escape minimale per MarkdownV2 di Telegram."""
    if not text:
        return ""
    specials = r"_*[]()~`>#+-=|{}.!\\"
    out = []
    for ch in text:
        if ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)
