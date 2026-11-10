# Bot Telegram – Link di sessione per multi-server

Bot Telegram che genera **link di sessione** per gli utenti verso le tue app dietro
reverse proxy Nginx, usando sottodomini casuali su domini con wildcard già puntato a
Cloudflare. Il superadmin abilita manualmente ogni utente al primo accesso e gestisce
server e domini.

## Come funziona

1. Hai uno o più server. Ogni server ha uno o più domini (es. `simone.net`).
2. Per ogni dominio, su Cloudflare esiste un record wildcard `*.simone.net` (proxied)
   che punta all'IP del server. **Non serve toccare il DNS per ogni sessione.**
3. Sul server, Nginx accetta qualunque sottodominio e lo inoltra alla tua app
   (vedi [`nginx.example.conf`](nginx.example.conf)). Il sottodominio = identità della sessione.
4. L'utente avvia il bot → la richiesta arriva a te (superadmin) → tu accetti o rifiuti.
5. Abiliti l'utente ai server che vuoi con `/grant <id_utente> <id_server>`.
   Un utente abilitato **solo** al server 1 riceverà link solo dai domini del server 1.
6. L'utente usa `/link` e riceve un URL tipo `http://abc123xy.simone.net`.

> Permessi a due livelli: l'approvazione (`/pending`) dà accesso al bot; `/grant`
> decide **per quali server** quell'utente può generare link. Senza almeno un `grant`,
> l'utente non ottiene nessun link.

> TLS: l'app interna parla in HTTP; il certificato lo gestisce Cloudflare (modalità
> SSL **Flexible**). I link generati usano `http://` come richiesto. Se un giorno vuoi
> `https://`, basta impostare `LINK_SCHEME=https` nel `.env`.

## Comandi

**Utente abilitato**
- `/start` – richiede l'accesso (la prima volta)
- `/link` – genera un link di sessione su un dominio casuale
- `/link <id_server>` – genera un link usando solo i domini di quel server
- `/miei` – mostra i tuoi ultimi link

**Superadmin**
- `/pending` – richieste di accesso in attesa (con pulsanti Accetta/Rifiuta)
- `/utenti` – elenco utenti e stato
- `/grant <id_utente> <id_server>` – abilita un utente a uno specifico server
- `/revoke <id_utente> <id_server>` – disabilita un utente da un server
- `/permessi <id_utente>` – mostra i server abilitati per un utente
- `/addserver <nome>` – aggiunge un server
- `/server` – elenca i server (con numero di domini)
- `/delserver <id_server>` – elimina un server e i suoi domini
- `/adddomain <id_server> <dominio>` – assegna un dominio a un server
- `/deldomain <id_dominio>` – elimina un dominio
- `/domini` – elenca i domini con i loro id
- `/sessioni` – ultimi link generati (con l'utente)

Esempio multi-server:
```
/addserver Server1
/addserver Server2
/adddomain 1 simone.net
/adddomain 1 esempio.net
/adddomain 1 altro.net
/adddomain 2 secondo.net
/adddomain 2 terzo.net
```

## Configurazione

1. Crea il bot con [@BotFather](https://t.me/BotFather) e copia il token.
2. Trova il tuo user ID con [@userinfobot](https://t.me/userinfobot).
3. Copia `.env.example` in `.env` e compila `BOT_TOKEN` e `SUPERADMIN_ID`.

## Avvio

### Con Docker (consigliato, su uno dei tuoi server)
```bash
docker compose up -d --build
docker compose logs -f
```

### Senza Docker
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

## Hosting

Il **codice** sta su GitHub; il **processo** del bot è in long-polling, quindi va
eseguito su un host sempre acceso (es. uno dei tuoi server, via Docker o systemd).
GitHub Actions non è adatto a un processo persistente.

Esempio unità systemd (`/etc/systemd/system/session-bot.service`):
```ini
[Unit]
Description=Telegram session bot
After=network-online.target

[Service]
WorkingDirectory=/opt/session-bot
EnvironmentFile=/opt/session-bot/.env
ExecStart=/opt/session-bot/.venv/bin/python -m bot.main
Restart=always

[Install]
WantedBy=multi-user.target
```

## Note di sicurezza
- `.env` e `data/` sono in `.gitignore`: non committare token né database.
- Solo il `SUPERADMIN_ID` può gestire utenti, server e domini.
- Gli utenti non abilitati non possono generare alcun link.
