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

## Files to upload to hosting

- `main.py`, `app/`, `requirements.txt`
- `bot_storage/bot_state.sqlite3`
- `config/.env` (or environment variables set on the host)

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

If you move the bot to another host, copy the whole `bot_storage/` folder too.

### Deploying on Render (or other hosts with an ephemeral filesystem)

Render's free web-service tier recreates the container's filesystem on every
restart/redeploy, so the SQLite file above gets wiped and the bot "forgets"
everything. To avoid that, set the `DATABASE_URL` environment variable to a
Postgres connection string (a free project on [Neon](https://neon.tech) or
[Supabase](https://supabase.com) works well). When `DATABASE_URL` is set, the
bot stores all the same state (profiles, bans, runtime snapshot) in that
Postgres database instead of the local SQLite file, so restarts/redeploys no
longer lose data. Nothing else changes — same schema, same behavior.

If you're on a paid Render plan with a persistent Disk, you can instead skip
`DATABASE_URL` and mount a Disk at `bot_storage/` so the SQLite file itself
persists.
