"""Raw sqlite3-backed persistence for bot_data (profiles, bans, runtime snapshot).

State is kept in ``context.application.bot_data`` at runtime and mirrored to
``bot_storage/bot_state.sqlite3`` so it survives restarts.
"""
import json
import logging
import os
import sqlite3

from telegram.ext import ContextTypes

from app.config import STATE_DB_PATH, STATE_DIR
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
            "INSERT OR REPLACE INTO runtime_state (id, data) VALUES (1, ?)",
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
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
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
                "INSERT OR REPLACE INTO profiles (user_id, data) VALUES (?, ?)",
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
                "INSERT OR REPLACE INTO banned_users (user_id, data) VALUES (?, ?)",
                (str(user_id), json.dumps(banned, ensure_ascii=False)),
            )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist ban record %s", user_id)


def _init_persistent_storage(app) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    for statement in ALL_SCHEMA_STATEMENTS:
        conn.execute(statement)

    app.bot_data["_state_db_connection"] = conn
    profiles = app.bot_data.setdefault("profiles", {})
    banned_users = app.bot_data.setdefault("banned_users", {})

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
