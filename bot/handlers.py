"""Handler dei comandi e dei callback del bot Telegram."""
from __future__ import annotations

import logging
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .config import Config
from .db import Database
from .utils import build_url, random_subdomain

log = logging.getLogger(__name__)

# Riempiti in setup_handlers()
_cfg: Config
_db: Database


def _is_admin(user_id: int) -> bool:
    return user_id == _cfg.superadmin_id


async def _reply(update: Update, text: str) -> None:
    msg = update.effective_message
    if msg:
        await msg.reply_text(text, disable_web_page_preview=True)


# ============================ /start ============================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if _is_admin(user.id):
        await _reply(
            update,
            "Ciao superadmin 👋\n\n"
            "Comandi admin:\n"
            "• /pending – richieste di accesso in attesa\n"
            "• /utenti – elenco utenti\n"
            "• /grant <id_utente> <id_server> – abilita un utente a un server\n"
            "• /revoke <id_utente> <id_server> – disabilita un utente da un server\n"
            "• /permessi <id_utente> – server abilitati per un utente\n"
            "• /addserver <nome> – aggiungi un server\n"
            "• /server – elenca i server\n"
            "• /adddomain <id_server> <dominio> – aggiungi un dominio a un server\n"
            "• /deldomain <id_dominio> – elimina un dominio\n"
            "• /delserver <id_server> – elimina un server e i suoi domini\n"
            "• /domini – elenca i domini (con i loro id)\n"
            "• /sessioni – ultime sessioni generate\n"
            "• /link – genera un link di sessione (anche tu puoi)",
        )
        return

    status = _db.upsert_pending_user(user.id, user.username, user.first_name)

    if status == "approved":
        await _reply(update, "Sei abilitato ✅\nUsa /link per ottenere un link di sessione.")
        return
    if status == "rejected":
        await _reply(update, "Il tuo accesso è stato rifiutato.")
        return
    if status == "banned":
        await _reply(update, "Il tuo accesso è stato revocato.")
        return

    # pending (nuovo o già in attesa)
    await _reply(
        update,
        "Richiesta inviata ✅\nUn amministratore deve abilitare il tuo accesso. "
        "Riceverai un messaggio appena viene approvata.",
    )
    await _notify_admin_new_request(context, user.id, user.username, user.first_name)


async def _notify_admin_new_request(context, uid, username, first_name) -> None:
    uname = f"@{username}" if username else "(nessun username)"
    text = (
        "🔔 Nuova richiesta di accesso\n\n"
        f"Nome: {first_name or '-'}\n"
        f"Username: {uname}\n"
        f"ID: {uid}"
    )
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Accetta", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject:{uid}"),
        ]]
    )
    try:
        await context.bot.send_message(_cfg.superadmin_id, text, reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        log.error("Impossibile notificare l'admin: %s", e)


# ============================ Callback approvazione ============================
async def cb_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user.id != _cfg.superadmin_id:
        if query:
            await query.answer("Non autorizzato", show_alert=True)
        return

    await query.answer()
    action, _, raw_uid = query.data.partition(":")
    if not raw_uid.isdigit():
        return
    uid = int(raw_uid)

    if action == "approve":
        _db.set_user_status(uid, "approved")
        await query.edit_message_text(query.message.text + "\n\n➡️ APPROVATO ✅")
        try:
            await context.bot.send_message(
                uid, "Sei stato abilitato ✅\nUsa /link per ottenere un link di sessione."
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Non ho potuto avvisare l'utente %s: %s", uid, e)
    elif action == "reject":
        _db.set_user_status(uid, "rejected")
        await query.edit_message_text(query.message.text + "\n\n➡️ RIFIUTATO ❌")
        try:
            await context.bot.send_message(uid, "La tua richiesta di accesso è stata rifiutata.")
        except Exception:  # noqa: BLE001
            pass


# ============================ /link (utente) ============================
async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not _is_admin(user.id) and not _db.is_approved(user.id):
        await _reply(update, "Non sei abilitato. Usa /start per richiedere l'accesso.")
        return

    # Scelta del dominio: opzionale filtro per id server (/link <id_server>)
    server_id = None
    if context.args and context.args[0].isdigit():
        server_id = int(context.args[0])

    domains = _db.list_domains(server_id)

    # L'admin può usare tutti i server; l'utente solo quelli autorizzati.
    if not _is_admin(user.id):
        allowed = _db.user_allowed_server_ids(user.id)
        if not allowed:
            await _reply(
                update,
                "Non sei abilitato a nessun server. Contatta l'amministratore.",
            )
            return
        domains = [d for d in domains if d["server_id"] in allowed]
        if server_id is not None and server_id not in allowed:
            await _reply(update, "Non sei abilitato a questo server.")
            return

    if not domains:
        if _is_admin(user.id):
            await _reply(update, "Nessun dominio configurato. Aggiungine uno con /adddomain.")
        else:
            await _reply(update, "Nessun dominio disponibile per i tuoi server. Contatta l'amministratore.")
        return

    domain_row = secrets.choice(domains)

    # Genera un sottodominio univoco per quel dominio
    sub = ""
    for _ in range(20):
        candidate = random_subdomain(_cfg.subdomain_length)
        if not _db.subdomain_exists(domain_row["id"], candidate):
            sub = candidate
            break
    if not sub:
        await _reply(update, "Errore nella generazione del link, riprova.")
        return

    url = build_url(_cfg.link_scheme, sub, domain_row["domain"])
    _db.add_session(user.id, domain_row["id"], sub, url)

    await _reply(update, f"Ecco il tuo link di sessione:\n\n{url}")


async def cmd_miei(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    rows = _db.list_sessions(user_id=user.id, limit=20)
    if not rows:
        await _reply(update, "Non hai ancora generato link.")
        return
    lines = ["I tuoi ultimi link:\n"]
    for r in rows:
        lines.append(f"• {r['full_url']}")
    await _reply(update, "\n".join(lines))


# ============================ Comandi admin ============================
def _require_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and _is_admin(user.id)


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    rows = _db.list_users(status="pending")
    if not rows:
        await _reply(update, "Nessuna richiesta in attesa.")
        return
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "(nessun username)"
        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Accetta", callback_data=f"approve:{r['telegram_id']}"),
                InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject:{r['telegram_id']}"),
            ]]
        )
        await context.bot.send_message(
            _cfg.superadmin_id,
            f"In attesa:\nNome: {r['first_name'] or '-'}\nUsername: {uname}\nID: {r['telegram_id']}",
            reply_markup=kb,
        )


async def cmd_utenti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    rows = _db.list_users()
    if not rows:
        await _reply(update, "Nessun utente registrato.")
        return
    lines = ["Utenti:\n"]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "-"
        lines.append(f"• {r['telegram_id']} {uname} [{r['status']}]")
    await _reply(update, "\n".join(lines))


async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await _reply(update, "Uso: /grant <id_utente> <id_server>")
        return
    uid, sid = int(context.args[0]), int(context.args[1])
    if _db.get_user(uid) is None:
        await _reply(update, f"Nessun utente con id {uid} (deve aver avviato il bot).")
        return
    if not any(s["id"] == sid for s in _db.list_servers()):
        await _reply(update, f"Nessun server con id {sid}.")
        return
    _db.grant_server(uid, sid)
    await _reply(update, f"Utente {uid} abilitato al server {sid} ✅")
    try:
        await context.bot.send_message(uid, "Sei stato abilitato a un nuovo server. Usa /link.")
    except Exception:  # noqa: BLE001
        pass


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await _reply(update, "Uso: /revoke <id_utente> <id_server>")
        return
    uid, sid = int(context.args[0]), int(context.args[1])
    n = _db.revoke_server(uid, sid)
    if n:
        await _reply(update, f"Utente {uid} disabilitato dal server {sid} ✅")
    else:
        await _reply(update, "L'utente non era abilitato a quel server.")


async def cmd_permessi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await _reply(update, "Uso: /permessi <id_utente>")
        return
    uid = int(context.args[0])
    if _db.get_user(uid) is None:
        await _reply(update, f"Nessun utente con id {uid}.")
        return
    servers = _db.list_user_servers(uid)
    if not servers:
        await _reply(update, f"L'utente {uid} non è abilitato a nessun server.")
        return
    lines = [f"Server abilitati per l'utente {uid}:\n"]
    for s in servers:
        lines.append(f"• id {s['id']}: {s['name']}")
    await _reply(update, "\n".join(lines))


async def cmd_addserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if not context.args:
        await _reply(update, "Uso: /addserver <nome>")
        return
    name = " ".join(context.args).strip()
    try:
        sid = _db.add_server(name)
        await _reply(update, f"Server aggiunto ✅  (id {sid}: {name})")
    except Exception:  # noqa: BLE001
        await _reply(update, "Errore: forse un server con questo nome esiste già.")


async def cmd_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    rows = _db.list_servers()
    if not rows:
        await _reply(update, "Nessun server. Aggiungine uno con /addserver <nome>.")
        return
    lines = ["Server:\n"]
    for r in rows:
        dn = len(_db.list_domains(r["id"]))
        lines.append(f"• id {r['id']}: {r['name']}  ({dn} domini)")
    await _reply(update, "\n".join(lines))


async def cmd_adddomain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await _reply(update, "Uso: /adddomain <id_server> <dominio>\nEs: /adddomain 1 simone.net")
        return
    server_id = int(context.args[0])
    domain = context.args[1].strip().lower().lstrip("*.").strip(".")
    if "." not in domain:
        await _reply(update, "Dominio non valido.")
        return
    if not any(s["id"] == server_id for s in _db.list_servers()):
        await _reply(update, f"Nessun server con id {server_id}.")
        return
    try:
        did = _db.add_domain(server_id, domain)
        await _reply(update, f"Dominio aggiunto ✅  (id {did}: {domain} → server {server_id})")
    except Exception:  # noqa: BLE001
        await _reply(update, "Errore: forse il dominio esiste già.")


async def cmd_deldomain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await _reply(update, "Uso: /deldomain <id_dominio>  (vedi gli id con /domini)")
        return
    did = int(context.args[0])
    n = _db.delete_domain(did)
    if n:
        await _reply(update, f"Dominio id {did} eliminato ✅")
    else:
        await _reply(update, f"Nessun dominio con id {did}.")


async def cmd_delserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await _reply(update, "Uso: /delserver <id_server>  (elimina anche i suoi domini)")
        return
    sid = int(context.args[0])
    dcount = len(_db.list_domains(sid))
    n = _db.delete_server(sid)
    if n:
        await _reply(update, f"Server id {sid} eliminato ✅  (rimossi anche {dcount} domini)")
    else:
        await _reply(update, f"Nessun server con id {sid}.")


async def cmd_domini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    rows = _db.list_domains()
    if not rows:
        await _reply(update, "Nessun dominio. Aggiungine uno con /adddomain <id_server> <dominio>.")
        return
    lines = ["Domini:\n"]
    for r in rows:
        lines.append(f"• id {r['id']}: *.{r['domain']}  (server {r['server_name']})")
    await _reply(update, "\n".join(lines))


async def cmd_sessioni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_admin(update):
        return
    rows = _db.list_sessions(limit=30)
    if not rows:
        await _reply(update, "Nessuna sessione generata.")
        return
    lines = ["Ultime sessioni:\n"]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else str(r["user_id"])
        lines.append(f"• {r['full_url']}  ({uname})")
    await _reply(update, "\n".join(lines))


# ============================ Registrazione ============================
def setup_handlers(app: Application, cfg: Config, db: Database) -> None:
    global _cfg, _db
    _cfg = cfg
    _db = db

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("miei", cmd_miei))

    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("utenti", cmd_utenti))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("permessi", cmd_permessi))
    app.add_handler(CommandHandler("addserver", cmd_addserver))
    app.add_handler(CommandHandler("server", cmd_server))
    app.add_handler(CommandHandler("adddomain", cmd_adddomain))
    app.add_handler(CommandHandler("deldomain", cmd_deldomain))
    app.add_handler(CommandHandler("delserver", cmd_delserver))
    app.add_handler(CommandHandler("domini", cmd_domini))
    app.add_handler(CommandHandler("sessioni", cmd_sessioni))

    app.add_handler(CallbackQueryHandler(cb_decision, pattern=r"^(approve|reject):\d+$"))
