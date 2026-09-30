"""Create a local Telethon session for the configured owner account."""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "config" / ".env")


async def main() -> None:
    api_id_value = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    expected_user_id_value = os.environ.get("TELEGRAM_LOGIN_USER_ID", "").strip()
    if not api_id_value or not api_hash or not expected_user_id_value:
        raise RuntimeError(
            "Set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_LOGIN_USER_ID "
            "in config/.env before running loginowner.py."
        )

    try:
        api_id = int(api_id_value)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer.") from exc
    try:
        expected_user_id = int(expected_user_id_value)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_LOGIN_USER_ID must be an integer Telegram account ID.") from exc

    session_dir = BASE_DIR / "local_sessions"
    session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = TelegramClient(str(session_dir / "owner"), api_id, api_hash)
    try:
        await client.start()
        account = await client.get_me()
        if account.id != expected_user_id:
            raise RuntimeError(
                f"Logged-in Telegram account ID {account.id} does not match "
                "TELEGRAM_LOGIN_USER_ID configured in config/.env."
            )
        print(f"Owner account authorized successfully (Telegram ID: {account.id}).")
        print(f"Local session saved under {session_dir}. Keep it private and do not share it.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
