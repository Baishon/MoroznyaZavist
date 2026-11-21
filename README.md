# Telegram Bot

Telegram bot on `python-telegram-bot` v20+ with local SQLite persistence, organized as a package under `app/`.

## Project structure

```
main.py                    # entry point, run this
requirements.txt
config/.env                # TELEGRAM_TOKEN lives here (not committed)
bot_storage/bot_state.sqlite3
app/
  config.py                # env loading + constants (chat ids, admin titles, paths)
  loader.py                 # builds the Application and registers every handler
  logging_setup.py          # TelegramLogHandler + logging configuration
  database/
    models.py               # SQL schema (CREATE TABLE statements)
    requests.py              # sqlite connection + save/load helpers
  keyboards/
    inline.py                # inline/reply keyboard builders
  services/
    profiles.py              # profile helpers, stats text, ban/warn target resolution
    bans.py                   # ban enforcement
    mutes.py                  # admin mute helpers
    topics.py                 # forum-topic state helpers, suspicious-username detection
    messaging.py              # user<->admin message delivery
  handlers/
    user.py                   # private-chat / user-facing commands & callbacks
    admin.py                  # staff-group / log-chat commands & callbacks
  images/
    okak.png                  # main menu image
    profile.png               # user profile image
    Sunday.png                # weekly admin report image
site/
  index.html                  # animated Telegram login and protected homepage
```

`app/services/messaging.py` is shared by `handlers/user.py` and
`handlers/admin.py`, without either handler module depending on the other.

## Deployment

For Render, create a **Web Service** from this repository and select **Docker**.
The included `Dockerfile` starts the bot with `python main.py`. Use one instance
only: Telegram polling must not run in multiple replicas at the same time.

Add these environment variables in Render:

- `TELEGRAM_TOKEN` — the bot token, stored as a secret.
- `DATABASE_URL` — the **Internal Database URL** from a Render PostgreSQL
  database in the same region.

The bot serves a website at `/` and a health endpoint at `/healthz` on Render's
`$PORT` while receiving Telegram updates through polling. Website login uses
short-lived, one-time Telegram approval requests and requires an administrator
with level 4 or higher and the exact `👁Logs` prefix. Browser sessions are kept
in memory and expire after 12 hours, so a bot restart signs website users out.
Login links open `@Icyenvy_helper_bot`; the configured `TELEGRAM_TOKEN` must
belong to that bot. Use HTTPS when exposing the site publicly; the same-origin
API does not enable cross-origin access. After login, the dashboard shows the
Telegram account and profile photo when available, plus a read-only console
with `help`, `status`, and recent warning/error `logs` commands. It does not
execute operating-system commands or control the bot process. The `Logs` tab
lists known private-chat users and their recorded message history. Authorized
admins can send technical notices (prefixed with
`Сообщение от технического раздела:`) and delete private-chat messages.
The chat history is kept in a compact scrollable area, and the `История` view
shows up to 50 recent moderation and session actions recorded for the selected
user. Telegram permission and age restrictions still apply; only conversations
known to this bot are available.
The `База данных` tab looks up users by Telegram ID and shows their profile,
ban/block status, action history, registration data, and session history.
Administrators can change levels (level 4 may assign levels 1–3; level 5 may
assign levels 1–5), optionally send an onboarding message, remove admin rights
silently or with a 14-day ban, and manage up to three prefixes as a level-5
administrator. The separate `Сессии` tab lists recorded active and closed
sessions; active sessions allow sending technical-section messages and deleting
recorded Telegram messages, while closed sessions are read-only. Session message
history starts being recorded after this feature is deployed; Telegram does
not provide the bot with older topic history to backfill.
The main menu offers an admin-candidature application to eligible non-admin
users. Starting it requires confirmation, closes the user's active sessions,
and begins a five-step questionnaire; while filling it in, only the application
steps, `/restart`, and `/stop` are available. Submitted applications are posted
to the configured review topic. Level-4+ administrators can accept or reject
them there. Rejected applicants can reapply after seven days; accepted
applicants receive a one-use academy invite link that expires after one hour.

Do not upload `config/.env`, `.venv/`, or `bot_storage/` to Render. The
filesystem of a Web Service is not a database; PostgreSQL is used automatically
when `DATABASE_URL` is set.

## Setup

1. Install Python 3.11+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set the token in `config/.env`:

```bash
TELEGRAM_TOKEN=your_bot_token_here
```

4. Run the bot:

```bash
python main.py
```

## Persistence

By default the bot stores its state in `bot_storage/bot_state.sqlite3`.
It keeps profiles, bans, warnings, admin levels, last admin tags, and the profile sequence there.

If you move the bot to another host and want to preserve local development
data, copy the whole `bot_storage/` folder. On Render, use PostgreSQL instead.

### Deploying on Render (or other hosts with an ephemeral filesystem)

Render's Web Service filesystem can be recreated on restart or redeploy, so
SQLite is only intended for local development. When `DATABASE_URL` is set, the
bot creates its tables and stores profiles, bans, warnings, and the runtime
snapshot in PostgreSQL. This preserves state across restarts and redeploys.
