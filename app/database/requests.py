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


def _get_dashboard_messages(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    *,
    limit: int = 100,
) -> list[dict]:
    return _get_message_history(context, user_id=user_id, limit=limit)


def _record_user_session(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    session: dict,
) -> str:
    conn = _state_db_connection(context)
    if conn is None:
        raise RuntimeError("Persistent storage is not initialized")
    session_id = str(session.get("session_id") or uuid4().hex)
    session["session_id"] = session_id
    started_at = str(session.get("session_started_at") or datetime.now(timezone.utc).isoformat())
    conn.execute(
        "INSERT INTO user_session_history "
        "(session_id, user_id, admin_id, admin_name, chat_id, topic_id, mood, "
        "started_at, closed_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "admin_id = EXCLUDED.admin_id, admin_name = EXCLUDED.admin_name, "
        "chat_id = EXCLUDED.chat_id, topic_id = EXCLUDED.topic_id, mood = EXCLUDED.mood, "
        "started_at = EXCLUDED.started_at, closed_at = EXCLUDED.closed_at, status = EXCLUDED.status",
        (
            session_id,
            str(user_id),
            str(session.get("admin_id")) if session.get("admin_id") is not None else None,
            str(session.get("admin_tag") or session.get("admin_username") or "") or None,
            str(session.get("chat_id")) if session.get("chat_id") is not None else None,
            str(session.get("topic_id")) if session.get("topic_id") is not None else None,
            str(session.get("mood") or "") or None,
            started_at,
            session.get("closed_at"),
            "active" if session.get("active") else "closed",
        ),
    )
    conn.commit()
    return session_id


def _close_user_session(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    session: dict,
    *,
    closed_at: str | None = None,
) -> None:
    session["active"] = False
    session["closed_at"] = closed_at or datetime.now(timezone.utc).isoformat()
    _record_user_session(context, user_id, session)
    conn = _state_db_connection(context)
    conn.execute(
        "UPDATE user_session_history SET status = ?, closed_at = ? WHERE session_id = ?",
        ("closed", session["closed_at"], str(session["session_id"])),
    )
    conn.commit()


def _get_user_session_history(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: str | int | None = None,
    limit: int = 500,
) -> list[dict]:
    conn = _state_db_connection(context)
    if conn is None:
        raise RuntimeError("Persistent storage is not initialized")
    active_sessions = context.application.bot_data.get("active_chats", {}) or {}
    for active_user_id, session in active_sessions.items():
        if not isinstance(session, dict) or not session.get("active"):
            continue
        if user_id is not None and str(active_user_id) != str(user_id):
            continue
        _record_user_session(context, active_user_id, session)

    safe_limit = max(1, min(int(limit), 500))
    if user_id is None:
        rows = conn.execute(
            "SELECT session_id, user_id, admin_id, admin_name, chat_id, topic_id, "
            "mood, started_at, closed_at, status FROM user_session_history "
            "ORDER BY started_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT session_id, user_id, admin_id, admin_name, chat_id, topic_id, "
            "mood, started_at, closed_at, status FROM user_session_history "
            "WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (str(user_id), safe_limit),
        ).fetchall()

    active_ids = {
        str(session.get("session_id"))
        for session in active_sessions.values()
        if isinstance(session, dict) and session.get("active") and session.get("session_id")
    }
    results = []
    for row in rows:
        record = dict(zip(
            (
                "session_id", "user_id", "admin_id", "admin_name", "chat_id",
                "topic_id", "mood", "started_at", "closed_at", "status",
            ),
            row,
        ))
        if record["status"] == "active" and record["session_id"] not in active_ids:
            closed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE user_session_history SET status = ?, closed_at = ? WHERE session_id = ?",
                ("closed", closed_at, record["session_id"]),
            )
            record["status"] = "closed"
            record["closed_at"] = closed_at
        results.append(record)
    conn.commit()
    return results


def _record_user_session_message(
    context: ContextTypes.DEFAULT_TYPE,
    session: dict,
    message,
    actor=None,
    *,
    direction: str | None = None,
) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        logging.error("Cannot persist a session message: persistent storage is not initialized")
        return
    message_id = getattr(message, "message_id", None)
    session_id = session.get("session_id")
    if message_id is None or not session_id:
        return
    actor_id = getattr(actor, "id", None)
    actor_name = getattr(actor, "full_name", None) or getattr(actor, "username", None)
    if direction is None:
        direction = (
            "admin"
            if str(actor_id or "") == str(session.get("admin_id") or "")
            else "user"
        )
    message_type = "text" if getattr(message, "text", None) is not None else "other"
    conn.execute(
        "INSERT INTO user_session_messages "
        "(id, session_id, message_id, actor_id, actor_name, direction, message_type, "
        "text, caption, created_at, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (session_id, message_id) DO NOTHING",
        (
            str(uuid4()),
            str(session_id),
            str(message_id),
            str(actor_id) if actor_id is not None else None,
            str(actor_name) if actor_name else None,
            direction,
            message_type,
            getattr(message, "text", None),
            getattr(message, "caption", None),
            getattr(message, "date", None).isoformat()
            if getattr(message, "date", None) is not None
            else datetime.now(timezone.utc).isoformat(),
            0,
        ),
    )
    conn.commit()


def _get_user_session_messages(
    context: ContextTypes.DEFAULT_TYPE,
    session_id: str,
    *,
    limit: int = 100,
) -> list[dict]:
    conn = _state_db_connection(context)
    if conn is None:
        raise RuntimeError("Persistent storage is not initialized")
    safe_limit = max(1, min(int(limit), 200))
    rows = conn.execute(
        "SELECT message_id, actor_id, actor_name, direction, message_type, text, "
        "caption, created_at, deleted FROM user_session_messages "
        "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (str(session_id), safe_limit),
    ).fetchall()
    fields = (
        "message_id", "actor_id", "actor_name", "direction", "message_type",
        "text", "caption", "created_at", "deleted",
    )
    return [dict(zip(fields, row)) for row in reversed(rows)]


def _mark_user_session_message_deleted(
    context: ContextTypes.DEFAULT_TYPE,
    session_id: str,
    message_id: str,
) -> bool:
    conn = _state_db_connection(context)
    if conn is None:
        raise RuntimeError("Persistent storage is not initialized")
    cursor = conn.execute(
        "UPDATE user_session_messages SET deleted = ?, text = ?, caption = ? "
        "WHERE session_id = ? AND message_id = ?",
        (1, "[удалено через панель]", None, str(session_id), str(message_id)),
    )
    conn.commit()
    return cursor.rowcount > 0


def _record_user_action(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    action: str,
    *,
    actor_id: str | int | None = None,
    details: str | None = None,
) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        logging.error(
            "Cannot record user action %s for %s: persistent storage is not initialized",
            action,
            user_id,
        )
        return
    try:
        conn.execute(
            "INSERT INTO user_action_history "
            "(id, user_id, actor_id, action, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                str(user_id),
                str(actor_id) if actor_id is not None else None,
                str(action)[:80],
                str(details)[:1000] if details is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception:
        logging.exception("Failed to record user action %s for %s", action, user_id)
        try:
            conn.rollback()
        except Exception:
            logging.exception("Failed to roll back user action write for %s", user_id)


def _get_user_action_history(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    *,
    limit: int = 50,
) -> list[dict]:
    conn = _state_db_connection(context)
    if conn is None:
        raise RuntimeError("Persistent storage is not initialized")
    safe_limit = max(1, min(int(limit), 100))
    rows = conn.execute(
        "SELECT action, details, actor_id, created_at "
        "FROM user_action_history WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (str(user_id), safe_limit),
    ).fetchall()
    return [
        {
            "action": row[0],
            "details": row[1],
            "actor_id": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]


def _dashboard_message_action(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str | int,
    message_id: str | int,
    *,
    text: str | None = None,
    deleted: bool = False,
) -> bool:
    conn = _state_db_connection(context)
    if conn is None:
        return False
    if deleted:
        cursor = conn.execute(
            "UPDATE message_history SET text = ?, caption = ? "
            "WHERE chat_id = ? AND telegram_message_id = ?",
            ("[удалено через панель]", None, str(user_id), str(message_id)),
        )
    else:
        cursor = conn.execute(
            "UPDATE message_history SET text = ? "
            "WHERE chat_id = ? AND telegram_message_id = ? AND direction = ? "
            "AND message_type = ?",
            (text, str(user_id), str(message_id), "outgoing", "text"),
        )
    conn.commit()
    return cursor.rowcount > 0


def _get_dashboard_users(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    profiles = context.application.bot_data.get("profiles", {}) or {}
    normalized_query = query.casefold().lstrip("@")
    user_records = {}
    for user_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        username = str(profile.get("username") or "")
        nickname = str(profile.get("user_nickname") or "")
        display_name = nickname or (f"@{username}" if username and not username.startswith("id") else "")
        user_records[str(user_id)] = {
            "user_id": str(user_id),
            "username": username if not username.startswith("id") else None,
            "display_name": display_name or f"Пользователь {user_id}",
        }

    conn = _state_db_connection(context)
    if conn is not None:
        rows = conn.execute(
            "SELECT user_id, MAX(username), MAX(first_name), MAX(last_name) "
            "FROM message_history WHERE chat_type = ? AND direction = ? "
            "AND user_id IS NOT NULL GROUP BY user_id",
            ("private", "incoming"),
        ).fetchall()
        for user_id, username, first_name, last_name in rows:
            normalized_id = str(user_id)
            if normalized_id in user_records:
                continue
            display_name = " ".join(part for part in (first_name, last_name) if part)
            user_records[normalized_id] = {
                "user_id": normalized_id,
                "username": username,
                "display_name": display_name or (f"@{username}" if username else f"Пользователь {normalized_id}"),
            }

    users = []
    for user in user_records.values():
        username = user["username"] or ""
        display_name = user["display_name"]
        user_id = user["user_id"]
        if normalized_query and normalized_query not in " ".join(
            (str(user_id), username, display_name)
        ).casefold():
            continue
        users.append(user)
    users.sort(key=lambda user: (user["display_name"].casefold(), user["user_id"]))
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    return users[safe_offset:safe_offset + safe_limit]


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

        legacy_candidate_data = False
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            candidate_status = str(profile.get("admin_candidate_status") or "")
            was_unapproved_candidate = bool(profile.get("admin_candidate")) or candidate_status in {
                "onboarding",
                "pending_review",
            }
            if was_unapproved_candidate:
                try:
                    if int(profile.get("admin_level", 0) or 0) > 0:
                        profile["admin_level"] = 0
                        profile["admin_rank"] = ""
                        profile.pop("prefix", None)
                        profile.pop("prefixes", None)
                    profile.pop("admin_candidate", None)
                    profile.pop("admin_candidate_status", None)
                    conn.execute(
                        "UPDATE profiles SET data = ? WHERE user_id = ?",
                        (json.dumps(profile, ensure_ascii=False), str(user_id)),
                    )
                    legacy_candidate_data = True
                    logging.info("Removed unapproved legacy admin candidate state for user %s", user_id)
                except Exception:
                    logging.exception("Failed to clean legacy admin candidate profile %s", user_id)
            elif "admin_candidate" in profile or "admin_candidate_status" in profile:
                profile.pop("admin_candidate", None)
                profile.pop("admin_candidate_status", None)
                conn.execute(
                    "UPDATE profiles SET data = ? WHERE user_id = ?",
                    (json.dumps(profile, ensure_ascii=False), str(user_id)),
                )
                legacy_candidate_data = True

        for key in (
            "admin_candidate_state",
            "pending_admin_candidate_reviews",
            "pending_candidate_rejections",
        ):
            if app.bot_data.pop(key, None) is not None:
                legacy_candidate_data = True

        if legacy_candidate_data:
            snapshot = {
                key: _json_safe(value)
                for key, value in app.bot_data.items()
                if key != "_state_db_connection"
            }
            conn.execute(
                "UPDATE runtime_state SET data = ? WHERE id = 1",
                (json.dumps(snapshot, ensure_ascii=False),),
            )
            conn.commit()
    except Exception:
        logging.exception("Failed to load persistent state")
