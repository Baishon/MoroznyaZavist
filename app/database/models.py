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

ALL_SCHEMA_STATEMENTS = (
    META_TABLE_SQL,
    PROFILES_TABLE_SQL,
    BANNED_USERS_TABLE_SQL,
    RUNTIME_STATE_TABLE_SQL,
)
