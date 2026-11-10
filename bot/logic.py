"""Logica dei comandi del bot, indipendente dal trasporto (webhook o polling).

process_update(update, api, db, cfg) riceve un update Telegram grezzo (dict),
esegue l'azione e invia le risposte tramite `api`.
"""
from __future__ import annotations

import logging
import secrets
import urllib.parse

from .config import Config
from .db import Database
from .telegram_api import TelegramAPI, inline_keyboard
from .utils import build_url, random_subdomain

log = logging.getLogger(__name__)


def process_update(update: dict, api: TelegramAPI, db: Database, cfg: Config) -> None:
    if "message" in update:
        _handle_message(update["message"], api, db, cfg)
    elif "callback_query" in update:
        _handle_callback(update["callback_query"], api, db, cfg)


# ============================ Messaggi / comandi ============================
def _handle_message(msg: dict, api: TelegramAPI, db: Database, cfg: Config) -> None:
    text = (msg.get("text") or "").strip()
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    uid = user.get("id")
    if uid is None or chat_id is None:
        return

    if not text.startswith("/"):
        # Messaggio normale: se è un URL con credenziali, lo "ripulisce".
        low = text.lower()
        if low.startswith("http") and ("username=" in low or "password=" in low
                                       or "user=" in low or "pass=" in low or "@" in text):
            handle_clean_url(text, api, db, cfg, chat_id, uid, user)
        return

    parts = text.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1:]

    handler = _COMMANDS.get(cmd)
    if handler is None:
        return
    handler(api, db, cfg, chat_id, uid, user, args)


def _is_admin(cfg: Config, uid: int) -> bool:
    return cfg.is_admin(uid)


def _admin_only(fn):
    def wrapper(api, db, cfg, chat_id, uid, user, args):
        if not _is_admin(cfg, uid):
            return
        fn(api, db, cfg, chat_id, uid, user, args)
    return wrapper


# ---------------- /start ----------------
def cmd_start(api, db, cfg, chat_id, uid, user, args):
    if _is_admin(cfg, uid):
        api.send_message(chat_id, _ADMIN_HELP)
        return

    status = db.upsert_pending_user(uid, user.get("username"), user.get("first_name"))
    if status == "approved":
        api.send_message(chat_id, "Sei abilitato ✅\nUsa /link per ottenere un link di sessione.")
        return
    if status in ("rejected", "banned"):
        api.send_message(chat_id, "Il tuo accesso non è attivo. Contatta l'amministratore.")
        return

    api.send_message(
        chat_id,
        "Richiesta inviata ✅\nUn amministratore deve abilitare il tuo accesso. "
        "Riceverai un messaggio appena viene approvata.",
    )
    _notify_admin_new_request(api, cfg, uid, user.get("username"), user.get("first_name"))


def _notify_admin_new_request(api, cfg, uid, username, first_name):
    uname = f"@{username}" if username else "(nessun username)"
    text = (
        "🔔 Nuova richiesta di accesso\n\n"
        f"Nome: {first_name or '-'}\n"
        f"Username: {uname}\n"
        f"ID: {uid}"
    )
    kb = inline_keyboard([[("✅ Accetta", f"approve:{uid}"), ("❌ Rifiuta", f"reject:{uid}")]])
    for admin_id in cfg.superadmin_ids:
        api.send_message(admin_id, text, reply_markup=kb)


# ---------------- /link ----------------
def _available_servers(db, cfg, uid) -> list:
    """Server utilizzabili dall'utente (con almeno un dominio)."""
    servers = [s for s in db.list_servers() if db.list_domains(s["id"])]
    if not _is_admin(cfg, uid):
        allowed = db.user_allowed_server_ids(uid)
        servers = [s for s in servers if s["id"] in allowed]
    return servers


def cmd_link(api, db, cfg, chat_id, uid, user, args):
    if not _is_admin(cfg, uid) and not db.is_approved(uid):
        api.send_message(chat_id, "Non sei abilitato. Usa /start per richiedere l'accesso.")
        return

    # Con un numero (/link 2) genera subito da quel server.
    if args and args[0].isdigit():
        _generate_and_send_link(api, db, cfg, chat_id, uid, user, int(args[0]))
        return

    # Senza numero: mostra i pulsanti dei server disponibili.
    servers = _available_servers(db, cfg, uid)
    if not servers:
        # Nessun server/dominio: delega al generatore che invia il messaggio giusto.
        _generate_and_send_link(api, db, cfg, chat_id, uid, user, None)
        return

    rows = [[(f"🖥 {s['name']}", f"link:{s['id']}")] for s in servers]
    api.send_message(chat_id, "Da quale server vuoi il link?", reply_markup=inline_keyboard(rows))


def _make_link(db, cfg, uid, user, server_id):
    """Genera un link. Ritorna (url, None) oppure (None, messaggio_errore)."""
    domains = db.list_domains(server_id)

    if not _is_admin(cfg, uid):
        allowed = db.user_allowed_server_ids(uid)
        if not allowed:
            return None, "Non sei abilitato a nessun server. Contatta l'amministratore."
        if server_id is not None and server_id not in allowed:
            return None, "Non sei abilitato a questo server."
        domains = [d for d in domains if d["server_id"] in allowed]

    if not domains:
        if _is_admin(cfg, uid):
            return None, "Nessun dominio configurato. Aggiungine uno con /adddomain."
        return None, "Nessun dominio disponibile per i tuoi server. Contatta l'amministratore."

    # Garantisce che chi genera il link esista in `users` (vincolo di sessions).
    db.ensure_user(uid, user.get("username"), user.get("first_name"), approved=_is_admin(cfg, uid))

    domain_row = secrets.choice(domains)
    sub = ""
    for _ in range(20):
        candidate = random_subdomain(cfg.subdomain_length)
        if not db.subdomain_exists(domain_row["id"], candidate):
            sub = candidate
            break
    if not sub:
        return None, "Errore nella generazione del link, riprova."

    url = build_url(cfg.link_scheme, sub, domain_row["domain"])
    db.add_session(uid, domain_row["id"], sub, url)
    return url, None


def _generate_and_send_link(api, db, cfg, chat_id, uid, user, server_id):
    """Genera e invia un link; server_id None = dominio casuale tra quelli disponibili."""
    url, err = _make_link(db, cfg, uid, user, server_id)
    if err:
        api.send_message(chat_id, err)
        return
    api.send_message(chat_id, f"Ecco il tuo link di sessione:\n\n{url}")


# ---------------- Ripulisci un URL con credenziali ----------------
_MD2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def _md2(text: str) -> str:
    """Escape per testo normale in MarkdownV2."""
    return "".join("\\" + c if c in _MD2_SPECIAL else c for c in text)


def _md2_code(text: str) -> str:
    """Escape per contenuto dentro `code` in MarkdownV2 (solo ` e \\)."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _extract_credentials(token: str) -> tuple[str, str]:
    """Estrae (username, password) da un URL via query string o userinfo."""
    try:
        parsed = urllib.parse.urlparse(token)
        qs = urllib.parse.parse_qs(parsed.query)
    except Exception:  # noqa: BLE001
        return "", ""

    def pick(*keys):
        for k in keys:
            for qk, qv in qs.items():
                if qk.lower() == k and qv:
                    return qv[0]
        return ""

    username = pick("username", "user", "usr", "login", "email") or (parsed.username or "")
    password = pick("password", "pass", "pwd", "pw") or (parsed.password or "")
    return username, password


def handle_clean_url(text, api, db, cfg, chat_id, uid, user):
    """Riceve un URL con credenziali, le estrae e genera un nuovo link pulito."""
    if not _is_admin(cfg, uid) and not db.is_approved(uid):
        api.send_message(chat_id, "Non sei abilitato. Usa /start per richiedere l'accesso.")
        return

    token = text.split()[0] if text.split() else text
    username, password = _extract_credentials(token)
    if not username and not password:
        return  # non è un link con credenziali: ignora

    url, err = _make_link(db, cfg, uid, user, None)
    if err:
        api.send_message(chat_id, err)
        return

    parts = [_md2("Dati puliti (tocca per copiare):"), ""]
    parts.append(f"🔗 Link: `{_md2_code(url)}`")
    if username:
        parts.append(f"👤 Utente: `{_md2_code(username)}`")
    if password:
        parts.append(f"🔑 Password: `{_md2_code(password)}`")
    api.send_message(chat_id, "\n".join(parts), parse_mode="MarkdownV2")


def cmd_miei(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_sessions(user_id=uid, limit=20)
    if not rows:
        api.send_message(chat_id, "Non hai ancora generato link.")
        return
    lines = ["I tuoi ultimi link:\n"] + [f"• {r['full_url']}" for r in rows]
    api.send_message(chat_id, "\n".join(lines))


# ---------------- Admin: utenti e permessi ----------------
@_admin_only
def cmd_pending(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_users(status="pending")
    if not rows:
        api.send_message(chat_id, "Nessuna richiesta in attesa.")
        return
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "(nessun username)"
        kb = inline_keyboard([[("✅ Accetta", f"approve:{r['telegram_id']}"),
                               ("❌ Rifiuta", f"reject:{r['telegram_id']}")]])
        api.send_message(
            chat_id,
            f"In attesa:\nNome: {r['first_name'] or '-'}\nUsername: {uname}\nID: {r['telegram_id']}",
            reply_markup=kb,
        )


@_admin_only
def cmd_utenti(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_users()
    if not rows:
        api.send_message(chat_id, "Nessun utente registrato.")
        return
    lines = ["Utenti:\n"]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "-"
        lines.append(f"• {r['telegram_id']} {uname} [{r['status']}]")
    api.send_message(chat_id, "\n".join(lines))


@_admin_only
def cmd_grant(api, db, cfg, chat_id, uid, user, args):
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        api.send_message(chat_id, "Uso: /grant <id_utente> <id_server>")
        return
    tuid, sid = int(args[0]), int(args[1])
    if db.get_user(tuid) is None:
        api.send_message(chat_id, f"Nessun utente con id {tuid} (deve aver avviato il bot).")
        return
    if not any(s["id"] == sid for s in db.list_servers()):
        api.send_message(chat_id, f"Nessun server con id {sid}.")
        return
    db.grant_server(tuid, sid)
    api.send_message(chat_id, f"Utente {tuid} abilitato al server {sid} ✅")
    api.send_message(tuid, "Sei stato abilitato a un nuovo server. Usa /link.")


@_admin_only
def cmd_revoke(api, db, cfg, chat_id, uid, user, args):
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        api.send_message(chat_id, "Uso: /revoke <id_utente> <id_server>")
        return
    tuid, sid = int(args[0]), int(args[1])
    n = db.revoke_server(tuid, sid)
    api.send_message(chat_id, f"Utente {tuid} disabilitato dal server {sid} ✅" if n
                     else "L'utente non era abilitato a quel server.")


@_admin_only
def cmd_permessi(api, db, cfg, chat_id, uid, user, args):
    if not args or not args[0].isdigit():
        api.send_message(chat_id, "Uso: /permessi <id_utente>")
        return
    tuid = int(args[0])
    if db.get_user(tuid) is None:
        api.send_message(chat_id, f"Nessun utente con id {tuid}.")
        return
    servers = db.list_user_servers(tuid)
    if not servers:
        api.send_message(chat_id, f"L'utente {tuid} non è abilitato a nessun server.")
        return
    lines = [f"Server abilitati per l'utente {tuid}:\n"] + [f"• id {s['id']}: {s['name']}" for s in servers]
    api.send_message(chat_id, "\n".join(lines))


# ---------------- Admin: server e domini ----------------
@_admin_only
def cmd_addserver(api, db, cfg, chat_id, uid, user, args):
    if not args:
        api.send_message(chat_id, "Uso: /addserver <nome>")
        return
    name = " ".join(args).strip()
    try:
        sid = db.add_server(name)
        api.send_message(chat_id, f"Server aggiunto ✅  (id {sid}: {name})")
    except Exception:  # noqa: BLE001
        api.send_message(chat_id, "Errore: forse un server con questo nome esiste già.")


@_admin_only
def cmd_server(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_servers()
    if not rows:
        api.send_message(chat_id, "Nessun server. Aggiungine uno con /addserver <nome>.")
        return
    lines = ["Server:\n"]
    for r in rows:
        dn = len(db.list_domains(r["id"]))
        lines.append(f"• id {r['id']}: {r['name']}  ({dn} domini)")
    api.send_message(chat_id, "\n".join(lines))


@_admin_only
def cmd_delserver(api, db, cfg, chat_id, uid, user, args):
    if not args or not args[0].isdigit():
        api.send_message(chat_id, "Uso: /delserver <id_server>  (elimina anche i suoi domini)")
        return
    sid = int(args[0])
    dcount = len(db.list_domains(sid))
    n = db.delete_server(sid)
    api.send_message(chat_id, f"Server id {sid} eliminato ✅  (rimossi anche {dcount} domini)" if n
                     else f"Nessun server con id {sid}.")


@_admin_only
def cmd_adddomain(api, db, cfg, chat_id, uid, user, args):
    if len(args) < 2 or not args[0].isdigit():
        api.send_message(chat_id, "Uso: /adddomain <id_server> <dominio>\nEs: /adddomain 1 simone.net")
        return
    sid = int(args[0])
    domain = args[1].strip().lower().lstrip("*.").strip(".")
    if "." not in domain:
        api.send_message(chat_id, "Dominio non valido.")
        return
    if not any(s["id"] == sid for s in db.list_servers()):
        api.send_message(chat_id, f"Nessun server con id {sid}.")
        return
    try:
        did = db.add_domain(sid, domain)
        api.send_message(chat_id, f"Dominio aggiunto ✅  (id {did}: {domain} → server {sid})")
    except Exception:  # noqa: BLE001
        api.send_message(chat_id, "Errore: forse il dominio esiste già.")


@_admin_only
def cmd_deldomain(api, db, cfg, chat_id, uid, user, args):
    if not args or not args[0].isdigit():
        api.send_message(chat_id, "Uso: /deldomain <id_dominio>  (vedi gli id con /domini)")
        return
    did = int(args[0])
    n = db.delete_domain(did)
    api.send_message(chat_id, f"Dominio id {did} eliminato ✅" if n else f"Nessun dominio con id {did}.")


@_admin_only
def cmd_domini(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_domains()
    if not rows:
        api.send_message(chat_id, "Nessun dominio. Aggiungine uno con /adddomain <id_server> <dominio>.")
        return
    lines = ["Domini:\n"] + [f"• id {r['id']}: *.{r['domain']}  (server {r['server_name']})" for r in rows]
    api.send_message(chat_id, "\n".join(lines))


@_admin_only
def cmd_sessioni(api, db, cfg, chat_id, uid, user, args):
    rows = db.list_sessions(limit=30)
    if not rows:
        api.send_message(chat_id, "Nessuna sessione generata.")
        return
    lines = ["Ultime sessioni:\n"]
    for r in rows:
        uname = f"@{r['username']}" if r.get("username") else str(r["user_id"])
        lines.append(f"• {r['full_url']}  ({uname})")
    api.send_message(chat_id, "\n".join(lines))


# ============================ Callback (pulsanti) ============================
def _handle_callback(cb: dict, api: TelegramAPI, db: Database, cfg: Config) -> None:
    from_user = cb.get("from") or {}
    uid = from_user.get("id")
    cb_id = cb.get("id")
    data = cb.get("data") or ""
    message = cb.get("message") or {}
    msg_chat = (message.get("chat") or {}).get("id")
    msg_id = message.get("message_id")
    base_text = message.get("text") or ""

    action, _, rest = data.partition(":")

    # Pulsanti "scegli server" di /link: li può usare qualunque utente abilitato.
    if action == "link":
        if not _is_admin(cfg, uid) and not db.is_approved(uid):
            api.answer_callback_query(cb_id, "Non sei abilitato", show_alert=True)
            return
        api.answer_callback_query(cb_id)
        server_id = None if rest == "random" else (int(rest) if rest.isdigit() else None)
        _generate_and_send_link(api, db, cfg, msg_chat, uid, from_user, server_id)
        return

    # Pulsanti Accetta/Rifiuta: solo superadmin.
    if not cfg.is_admin(uid):
        api.answer_callback_query(cb_id, "Non autorizzato", show_alert=True)
        return
    api.answer_callback_query(cb_id)

    if not rest.isdigit():
        return
    target = int(rest)

    if action == "approve":
        db.set_user_status(target, "approved")
        if msg_chat and msg_id:
            api.edit_message_text(msg_chat, msg_id, base_text + "\n\n➡️ APPROVATO ✅")
        api.send_message(target, "Sei stato abilitato ✅\nUsa /link per ottenere un link di sessione.")
    elif action == "reject":
        db.set_user_status(target, "rejected")
        if msg_chat and msg_id:
            api.edit_message_text(msg_chat, msg_id, base_text + "\n\n➡️ RIFIUTATO ❌")
        api.send_message(target, "La tua richiesta di accesso è stata rifiutata.")


_ADMIN_HELP = (
    "Ciao superadmin 👋\n\n"
    "Utenti:\n"
    "• /pending – richieste in attesa\n"
    "• /utenti – elenco utenti\n"
    "• /grant <id_utente> <id_server> – abilita a un server\n"
    "• /revoke <id_utente> <id_server> – disabilita da un server\n"
    "• /permessi <id_utente> – server abilitati per un utente\n\n"
    "Server e domini:\n"
    "• /addserver <nome> – aggiungi server\n"
    "• /server – elenca server\n"
    "• /delserver <id_server> – elimina server\n"
    "• /adddomain <id_server> <dominio> – aggiungi dominio\n"
    "• /deldomain <id_dominio> – elimina dominio\n"
    "• /domini – elenca domini (con id)\n\n"
    "Altro:\n"
    "• /link – genera un link di sessione\n"
    "• /sessioni – ultimi link generati\n"
    "• Incolla un URL con credenziali → te lo ripulisce (link + utente + password copiabili)"
)

_COMMANDS = {
    "start": cmd_start,
    "link": cmd_link,
    "miei": cmd_miei,
    "pending": cmd_pending,
    "utenti": cmd_utenti,
    "grant": cmd_grant,
    "revoke": cmd_revoke,
    "permessi": cmd_permessi,
    "addserver": cmd_addserver,
    "server": cmd_server,
    "delserver": cmd_delserver,
    "adddomain": cmd_adddomain,
    "deldomain": cmd_deldomain,
    "domini": cmd_domini,
    "sessioni": cmd_sessioni,
}
