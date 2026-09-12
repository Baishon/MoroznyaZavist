"""SQLite schema for the bot's local persistence layer (no ORM is used).

Each constant is a ``CREATE TABLE IF NOT EXISTS`` statement executed once on
startup by :func:`app.database.requests.init_persistent_storage`.
"""

META_TABLE_SQL = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

PROFILES_TABLE_SQL = "CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)"

BANNED_USERS_TABLE_SQL = "CREATE TABLE IF NOT EXISTS banned_users (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)"

RUNTIME_STATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS runtime_state (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)"
)

MESSAGE_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS message_history (
    id TEXT PRIMARY KEY,
    update_id TEXT,
    telegram_message_id TEXT,
    user_id TEXT,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    chat_id TEXT,
    chat_type TEXT,
    direction TEXT NOT NULL,
    message_type TEXT NOT NULL,
    text TEXT,
    caption TEXT,
    callback_data TEXT,
    created_at TEXT NOT NULL
)
"""

MESSAGE_HISTORY_USER_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_message_history_user_id ON message_history (user_id)"
)

MESSAGE_HISTORY_CREATED_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_message_history_created_at ON message_history (created_at)"
)

MESSAGE_HISTORY_CHAT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_message_history_chat_id ON message_history (chat_id)"
)

ALL_SCHEMA_STATEMENTS = (
    META_TABLE_SQL,
    PROFILES_TABLE_SQL,
    BANNED_USERS_TABLE_SQL,
    RUNTIME_STATE_TABLE_SQL,
    MESSAGE_HISTORY_TABLE_SQL,
    MESSAGE_HISTORY_USER_INDEX_SQL,
    MESSAGE_HISTORY_CREATED_INDEX_SQL,
    MESSAGE_HISTORY_CHAT_INDEX_SQL,
)
