"""Bot configuration: secrets loaded from environment/.env and static constants."""
import os

from dotenv import load_dotenv

# Project root (one level above the `app` package).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, "config", ".env"))

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_TOKEN is not set. Put it in config/.env (TELEGRAM_TOKEN=...) "
        "or set it as an environment variable before starting the bot."
    )

# Chat for bot log messages.
LOG_CHAT_ID = -1004344722099
# Rules are published into this forum chat/topic.
RULES_CHAT_ID = -1004417963273
RULES_THREAD_ID = 209
# Owner allowed to run debug commands.
OWNER_ID = 7545068007
WORK_CHAT_ID = -1004417963273
COOPERATION_CHAT_ID = -1003918101019
OFFICIAL_CHANNEL_ID = -1003784351983

ADMIN_LEVEL_TITLES = {
    1: "🧸Стажер",
    2: "🦅Младший админ",
    3: "🐶Админ",
    4: "👮‍♀️Старший админ",
    5: "Руководство",
}

# Local SQLite persistence (kept at the project root as `bot_storage/`).
STATE_DIR = os.path.join(BASE_DIR, "bot_storage")
STATE_DB_PATH = os.path.join(STATE_DIR, "bot_state.sqlite3")
