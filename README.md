# Bot Telegram – Link di sessione multi-server (su Vercel)

Bot Telegram che genera **link di sessione** per gli utenti verso le tue app dietro
reverse proxy Nginx, usando sottodomini casuali su domini con wildcard già puntato a
Cloudflare. Il superadmin abilita manualmente ogni utente e lo autorizza a server specifici.

Hosting: **Vercel** (serverless, via webhook) + **Vercel Postgres** per i dati.
Nessun server sempre acceso: Telegram chiama Vercel ad ogni messaggio e il bot risponde subito.

## Come funziona

1. Hai uno o più server. Ogni server ha uno o più domini (es. `simone.net`).
2. Su Cloudflare esiste un record wildcard `*.simone.net` (proxied) che punta all'IP del
   server. **Non serve toccare il DNS per ogni sessione.**
3. Su ogni server, Nginx accetta qualunque sottodominio e lo inoltra alla tua app
   (vedi [`nginx.example.conf`](nginx.example.conf)). Il sottodominio = identità della sessione.
4. L'utente avvia il bot → la richiesta arriva a te (superadmin) → accetti o rifiuti.
5. Abiliti l'utente ai server voluti con `/grant <id_utente> <id_server>`.
6. L'utente usa `/link` e riceve un URL tipo `http://abc123xy.simone.net`.

> Permessi a due livelli: `/pending` dà accesso al bot; `/grant` decide **per quali server**
> l'utente può generare link. Senza almeno un `grant`, non ottiene nessun link.

## Comandi

**Utente abilitato**
- `/start` – richiede l'accesso (la prima volta)
- `/link` – genera un link su un dominio casuale (dei suoi server)
- `/link <id_server>` – forza un server specifico
- `/miei` – i tuoi ultimi link

**Superadmin**
- `/pending`, `/utenti`
- `/grant <id_utente> <id_server>`, `/revoke <id_utente> <id_server>`, `/permessi <id_utente>`
- `/addserver <nome>`, `/server`, `/delserver <id_server>`
- `/adddomain <id_server> <dominio>`, `/deldomain <id_dominio>`, `/domini`
- `/sessioni`

## Deploy su Vercel (passo-passo)

### 1. Crea il bot e prendi i dati
- Token da [@BotFather](https://t.me/BotFather).
- Il tuo user ID da [@userinfobot](https://t.me/userinfobot).

### 2. Importa il repo su Vercel
Vercel → **Add New… → Project** → importa `flix974-tech/bot-domini`. Non cambiare le
impostazioni di build (è un progetto Python serverless, viene rilevato da solo). Fai il
primo deploy.

### 3. Collega il database
Vercel → progetto → **Storage → Create Database → Postgres** → collegalo al progetto.
Vercel inietta da solo la variabile `POSTGRES_URL`. Non devi copiarla a mano.

### 4. Imposta le variabili d'ambiente
Vercel → progetto → **Settings → Environment Variables**, aggiungi:
| Nome | Valore |
|---|---|
| `BOT_TOKEN` | il token di BotFather |
| `SUPERADMIN_ID` | il tuo user ID Telegram |
| `WEBHOOK_SECRET` | una stringa a caso a tua scelta |
| `LINK_SCHEME` | `http` (o `https`) |

Poi fai **Redeploy** perché le variabili abbiano effetto.

### 5. Registra il webhook
Apri una volta nel browser (sostituisci dominio e il tuo SUPERADMIN_ID):
```
https://<tuo-progetto>.vercel.app/api/set_webhook?key=<SUPERADMIN_ID>
```
Deve rispondere `Esito: OK ✅`. Da questo momento il bot è attivo: scrivigli `/start`.

### 6. Prima configurazione da Telegram
Come superadmin: `/addserver`, `/adddomain`, poi approva gli utenti con `/pending`,
abilitali ai server con `/grant`.

## Sviluppo locale (opzionale)
Per testare senza Vercel, in long-polling:
```bash
pip install -r requirements.txt
cp .env.example .env     # compila BOT_TOKEN, SUPERADMIN_ID, POSTGRES_URL
python -m bot.main
```
Nota: polling e webhook sono mutuamente esclusivi. Dopo i test locali, riapri
`/api/set_webhook` per riattivare la modalità webhook su Vercel.

## Struttura
```
api/
  webhook.py       # endpoint che Telegram chiama (POST)
  set_webhook.py   # registra il webhook (apri una volta nel browser)
bot/
  config.py        # variabili d'ambiente
  db.py            # Postgres
  logic.py         # comandi del bot
  telegram_api.py  # client Bot API (urllib)
  utils.py         # sottodomini / URL
  main.py          # runner locale (polling)
nginx.example.conf # esempio reverse proxy sul server di origine
vercel.json
requirements.txt
```

## Sicurezza
- `.env` non va committato (è in `.gitignore`); su Vercel i segreti stanno nelle Env Vars.
- `WEBHOOK_SECRET` impedisce che chiunque invii finti update al tuo endpoint.
- Solo `SUPERADMIN_ID` gestisce utenti, server e domini.
