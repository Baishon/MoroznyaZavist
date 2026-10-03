"""Keep the owner's Telethon account connected without handling updates."""
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events


async def _mark_owner_message_read(event) -> None:
    try:
        await event.client.send_read_acknowledge(event.chat_id, max_id=event.id)
    except Exception:
        logging.exception(
            "Failed to mark owner-account message as read: chat_id=%s message_id=%s",
            event.chat_id,
            event.id,
        )


async def start_owner_session(app) -> None:
    api_id_value = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    expected_user_id_value = os.environ.get("TELEGRAM_LOGIN_USER_ID", "").strip()
    if not api_id_value and not api_hash and not expected_user_id_value:
        logging.info("Owner Telegram session is not configured; skipping user-account connection.")
        return
    if not api_id_value or not api_hash or not expected_user_id_value:
        logging.error(
            "Owner Telegram session is partially configured; set TELEGRAM_API_ID, "
            "TELEGRAM_API_HASH, and TELEGRAM_LOGIN_USER_ID."
        )
        return

    try:
        api_id = int(api_id_value)
        expected_user_id = int(expected_user_id_value)
    except ValueError:
        logging.exception("Owner Telegram session configuration contains a non-integer ID.")
        return

    session_path = Path(__file__).resolve().parents[2] / "local_sessions" / "owner"
    if not Path(f"{session_path}.session").is_file():
        logging.error(
            "Owner Telegram session file is missing: %s.session. "
            "Run loginowner.py on this server first.",
            session_path,
        )
        return

    client = TelegramClient(str(session_path), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logging.error("Owner Telegram session is not authorized; run loginowner.py again.")
            await client.disconnect()
            return

        account = await client.get_me()
        if not account or account.id != expected_user_id:
            actual_id = getattr(account, "id", None)
            logging.error(
                "Owner Telegram session ID mismatch: expected %s, got %s. Disconnecting.",
                expected_user_id,
                actual_id,
            )
            await client.disconnect()
            return

        client.add_event_handler(_mark_owner_message_read, events.NewMessage(incoming=True))
        app.bot_data["owner_telegram_client"] = client
        logging.info(
            "Owner Telegram session connected for account %s. Incoming messages will be marked read without replies.",
            account.id,
        )
    except Exception:
        logging.exception("Failed to connect owner Telegram session.")
        await client.disconnect()


async def stop_owner_session(app) -> None:
    client = app.bot_data.pop("owner_telegram_client", None)
    if client and client.is_connected():
        await client.disconnect()
        logging.info("Owner Telegram session disconnected.")
