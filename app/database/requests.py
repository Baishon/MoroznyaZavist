"""Persistence for bot_data (profiles, bans, runtime snapshot).

State is kept in ``context.application.bot_data`` at runtime and mirrored to
either Postgres (when ``DATABASE_URL`` is set) or the local
``bot_storage/bot_state.sqlite3`` file, so it survives restarts.
"""
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from telegram.ext import ContextTypes

from app.config import DATABASE_URL, STATE_DB_PATH, STATE_DIR
from app.database import db
from app.database.models import ALL_SCHEMA_STATEMENTS


def _state_db_connection(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("_state_db_connection")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _save_runtime_snapshot(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    snapshot = {
        key: _json_safe(value)
        for key, value in context.application.bot_data.items()
        if key != "_state_db_connection"
    }
    try:
        conn.execute(
            "INSERT INTO runtime_state (id, data) VALUES (1, ?) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (json.dumps(snapshot, ensure_ascii=False),),
        )
        conn.commit()
    except Exception:
        logging.exception("Failed to persist runtime snapshot")


def _save_profile_seq(context: ContextTypes.DEFAULT_TYPE, seq: int | None = None) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    value = int(seq if seq is not None else context.application.bot_data.get("profile_seq", 0) or 0)
    try:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            ("profile_seq", str(value)),
        )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist profile_seq")


def _save_profile_record(context: ContextTypes.DEFAULT_TYPE, user_id: str | int) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    profile = (context.application.bot_data.setdefault("profiles", {}) or {}).get(str(user_id))
    try:
        if profile is None:
            conn.execute("DELETE FROM profiles WHERE user_id = ?", (str(user_id),))
        else:
            conn.execute(
                "INSERT INTO profiles (user_id, data) VALUES (?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data",
                (str(user_id), json.dumps(profile, ensure_ascii=False)),
            )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist profile %s", user_id)


def _save_ban_record(context: ContextTypes.DEFAULT_TYPE, user_id: str | int) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    banned = (context.application.bot_data.setdefault("banned_users", {}) or {}).get(str(user_id))
    try:
        if banned is None:
            conn.execute("DELETE FROM banned_users WHERE user_id = ?", (str(user_id),))
        else:
            conn.execute(
                "INSERT INTO banned_users (user_id, data) VALUES (?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data",
                (str(user_id), json.dumps(banned, ensure_ascii=False)),
            )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist ban record %s", user_id)


def _save_incoming_message(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    update_id: int | None,
    message,
    actor=None,
    callback_data: str | None = None,
) -> None:
    """Persist one incoming Telegram message or callback for the future admin panel."""
    conn = _state_db_connection(context)
    if conn is None:
        return

    user = actor or getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)

    if callback_data is not None:
        message_type = "callback"
    elif getattr(message, "text", None) is not None:
        message_type = "text"
    elif getattr(message, "photo", None):
        message_type = "photo"
    elif getattr(message, "video", None):
        message_type = "video"
    elif getattr(message, "document", None):
        message_type = "document"
    elif getattr(message, "voice", None):
        message_type = "voice"
    elif getattr(message, "audio", None):
        message_type = "audio"
    elif getattr(message, "sticker", None):
        message_type = "sticker"
    else:
        message_type = "other"

    try:
        conn.execute(
            "INSERT INTO message_history ("
            "id, update_id, telegram_message_id, user_id, username, first_name, "
            "last_name, chat_id, chat_type, direction, message_type, text, caption, "
            "callback_data, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                str(update_id) if update_id is not None else None,
                str(getattr(message, "message_id", "")) or None,
                str(getattr(user, "id", "")) or None,
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                str(getattr(chat, "id", "")) or None,
                getattr(chat, "type", None),
                "incoming",
                message_type,
                text,
                caption,
                callback_data,
                getattr(message, "date", None).isoformat()
                if getattr(message, "date", None) is not None
                else None,
            ),
        )
        conn.commit()
    except Exception:
        logging.exception("Failed to persist incoming Telegram update")


def _save_outgoing_message(connection, message, recipient_chat_id=None) -> None:
    """Persist a successfully sent bot message without affecting delivery."""
    if connection is None or message is None:
        return

    chat = getattr(message, "chat", None)
    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    if getattr(message, "photo", None):
        message_type = "photo"
    elif getattr(message, "video", None):
        message_type = "video"
    elif getattr(message, "document", None):
        message_type = "document"
    elif getattr(message, "voice", None):
        message_type = "voice"
    elif getattr(message, "audio", None):
        message_type = "audio"
    elif getattr(message, "animation", None):
        message_type = "animation"
    elif getattr(message, "sticker", None):
        message_type = "sticker"
    elif getattr(message, "location", None):
        message_type = "location"
    elif text is not None:
        message_type = "text"
    else:
        message_type = "other"

    try:
        connection.execute(
            "INSERT INTO message_history ("
            "id, update_id, telegram_message_id, user_id, username, first_name, "
            "last_name, chat_id, chat_type, direction, message_type, text, caption, "
            "callback_data, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                None,
                str(getattr(message, "message_id", "")) or None,
                str(recipient_chat_id if recipient_chat_id is not None else getattr(chat, "id", "")) or None,
                None,
                None,
                None,
                str(getattr(chat, "id", "")) or None,
                getattr(chat, "type", None),
                "outgoing",
                message_type,
                text,
                caption,
                None,
                getattr(message, "date", None).isoformat()
                if getattr(message, "date", None) is not None
                else None,
            ),
        )
        connection.commit()
    except Exception:
        logging.exception("Failed to persist outgoing Telegram message")


def _save_outgoing_reference(
    connection,
    message_id,
    recipient_chat_id,
    message_type: str,
) -> None:
    """Persist a sent message when Telegram returns only its message ID."""
    if connection is None or message_id is None:
        return
    try:
        connection.execute(
            "INSERT INTO message_history ("
            "id, update_id, telegram_message_id, user_id, username, first_name, "
            "last_name, chat_id, chat_type, direction, message_type, text, caption, "
            "callback_data, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                None,
                str(message_id),
                str(recipient_chat_id) if recipient_chat_id is not None else None,
                None,
                None,
                None,
                str(recipient_chat_id) if recipient_chat_id is not None else None,
                None,
                "outgoing",
                message_type,
                None,
                None,
                None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        logging.exception("Failed to persist outgoing Telegram message reference")


def _get_message_history(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: str | int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Read message history in a stable shape for the future backend API."""
    conn = _state_db_connection(context)
    if conn is None:
        return []

    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    if user_id is None:
        cursor = conn.execute(
            "SELECT id, update_id, telegram_message_id, user_id, username, "
            "first_name, last_name, chat_id, chat_type, direction, message_type, "
            "text, caption, callback_data, created_at "
            "FROM message_history ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (safe_limit, safe_offset),
        )
    else:
        cursor = conn.execute(
            "SELECT id, update_id, telegram_message_id, user_id, username, "
            "first_name, last_name, chat_id, chat_type, direction, message_type, "
            "text, caption, callback_data, created_at "
            "FROM message_history WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (str(user_id), safe_limit, safe_offset),
        )

    columns = (
        "id", "update_id", "telegram_message_id", "user_id", "username",
        "first_name", "last_name", "chat_id", "chat_type", "direction",
        "message_type", "text", "caption", "callback_data", "created_at",
    )
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _init_persistent_storage(app) -> None:
    if not DATABASE_URL:
        os.makedirs(STATE_DIR, exist_ok=True)
    conn = db.connect(DATABASE_URL, STATE_DB_PATH)
    for statement in ALL_SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()

    app.bot_data["_state_db_connection"] = conn
    app.bot_data.setdefault("profiles", {})
    app.bot_data.setdefault("banned_users", {})

    try:
        row = conn.execute("SELECT data FROM runtime_state WHERE id = 1").fetchone()
        if row and row[0]:
            try:
                snapshot = json.loads(row[0])
                for key, value in snapshot.items():
                    if key != "_state_db_connection":
                        app.bot_data[key] = value
            except Exception:
                logging.exception("Failed to load runtime snapshot")

        # Read directly from app.bot_data (not a cached local) — the runtime_state
        # snapshot applied above may have just replaced these dicts wholesale.
        profiles = app.bot_data.setdefault("profiles", {})
        banned_users = app.bot_data.setdefault("banned_users", {})
        for user_id, data in conn.execute("SELECT user_id, data FROM profiles"):
            try:
                profiles[str(user_id)] = json.loads(data)
            except Exception:
                continue
        for user_id, data in conn.execute("SELECT user_id, data FROM banned_users"):
            try:
                banned_users[str(user_id)] = json.loads(data)
            except Exception:
                continue

        row = conn.execute("SELECT value FROM meta WHERE key = ?", ("profile_seq",)).fetchone()
        if row and str(row[0]).isdigit():
            app.bot_data["profile_seq"] = int(row[0])
    except Exception:
        logging.exception("Failed to load persistent state")
