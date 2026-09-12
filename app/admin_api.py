"""Read-only admin API for the future Android control panel."""
import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app.config import DATABASE_URL, STATE_DB_PATH
from app.database import db
from app.database.models import ALL_SCHEMA_STATEMENTS


def _open_connection():
    connection = db.connect(DATABASE_URL, STATE_DB_PATH)
    for statement in ALL_SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.commit()
    return connection


def create_admin_api() -> FastAPI:
    app = FastAPI(title="Telegram Bot Admin API", version="1.0.0")
    api_token = os.environ.get("ADMIN_API_TOKEN")
    owner_id = os.environ.get("ADMIN_OWNER_ID", "7545068007")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Martinez231107")

    def authorize(x_admin_token: str | None = Header(default=None)) -> None:
        if not api_token or not x_admin_token or not hmac.compare_digest(x_admin_token, api_token):
            raise HTTPException(status_code=401, detail="Invalid admin API token")

    @app.get("/")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(credentials: dict[str, str]) -> dict[str, str]:
        if (
            not hmac.compare_digest(credentials.get("owner_id", ""), owner_id)
            or not hmac.compare_digest(credentials.get("password", ""), admin_password)
        ):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not api_token:
            raise HTTPException(status_code=503, detail="ADMIN_API_TOKEN is not configured")
        return {"access_token": api_token, "token_type": "admin"}

    @app.get("/api/users", dependencies=[Depends(authorize)])
    def users(
        search: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        connection = _open_connection()
        try:
            rows = []
            profiles = connection.execute("SELECT user_id, data FROM profiles").fetchall()
            needle = search.casefold() if search else None
            for user_id, data in profiles:
                try:
                    import json
                    profile = json.loads(data)
                except (TypeError, ValueError):
                    continue
                haystack = " ".join(
                    str(profile.get(key, "")) for key in ("username", "first_name", "last_name", "id_profile")
                ).casefold()
                if needle and needle not in f"{user_id} {haystack}":
                    continue
                count = connection.execute(
                    "SELECT COUNT(*) FROM message_history WHERE user_id = ?", (str(user_id),)
                ).fetchone()[0]
                latest = connection.execute(
                    "SELECT MAX(created_at) FROM message_history WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()[0]
                rows.append(
                    {
                        "telegram_id": str(user_id),
                        "message_count": count,
                        "last_message_at": latest,
                        "profile": profile,
                    }
                )
            rows.sort(key=lambda item: item["last_message_at"] or "", reverse=True)
            return {"items": rows[offset : offset + limit], "total": len(rows)}
        finally:
            connection.close()

    @app.get("/api/users/{user_id}/messages", dependencies=[Depends(authorize)])
    def user_messages(
        user_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        connection = _open_connection()
        try:
            cursor = connection.execute(
                "SELECT id, update_id, telegram_message_id, user_id, username, "
                "first_name, last_name, chat_id, chat_type, direction, message_type, "
                "text, caption, callback_data, created_at FROM message_history "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (str(user_id), limit, offset),
            )
            columns = [item[0] for item in cursor.description]
            return {"items": [dict(zip(columns, row)) for row in cursor.fetchall()]}
        finally:
            connection.close()

    @app.get("/api/messages", dependencies=[Depends(authorize)])
    def messages(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        connection = _open_connection()
        try:
            cursor = connection.execute(
                "SELECT id, update_id, telegram_message_id, user_id, username, "
                "first_name, last_name, chat_id, chat_type, direction, message_type, "
                "text, caption, callback_data, created_at FROM message_history "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            columns = [item[0] for item in cursor.description]
            return {"items": [dict(zip(columns, row)) for row in cursor.fetchall()]}
        finally:
            connection.close()

    return app
