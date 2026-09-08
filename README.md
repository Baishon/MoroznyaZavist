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
  states/
    form.py                  # admin-candidate flow stage constants
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
    candidates.py             # admin-candidate approval flow (shared by user.py and admin.py)
```

`app/handlers/candidates.py` and `app/services/messaging.py` exist as neutral modules that both
`handlers/user.py` and `handlers/admin.py` depend on, without depending on each other — this avoids
a circular import between the two handler modules.

## Deployment

For Render, create a **Web Service** from this repository and select **Docker**.
The included `Dockerfile` starts the bot with `python main.py`. Use one instance
only: Telegram polling must not run in multiple replicas at the same time.

Add these environment variables in Render:

- `TELEGRAM_TOKEN` — the bot token, stored as a secret.
- `WEBHOOK_URL` — the public Render service URL, for example
  `https://your-service.onrender.com`.
- `DATABASE_URL` — the **Internal Database URL** from a Render PostgreSQL
  database in the same region.

Set the health check path to `/`. The bot listens on Render's `$PORT`, receives
Telegram POST requests at `/webhook`, and configures that webhook automatically
from `WEBHOOK_URL`. The `/set_webhook` command is restricted to `OWNER_ID`.

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
WEBHOOK_URL=https://your-service.onrender.com
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
