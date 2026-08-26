"""Connection helper that picks Postgres (if ``DATABASE_URL`` is set) or local SQLite.

Render's disk is ephemeral on the free tier, so state stored only in
``bot_storage/bot_state.sqlite3`` is wiped on every redeploy/restart. Pointing
``DATABASE_URL`` at a free managed Postgres (Neon, Supabase, etc.) keeps the
data outside the container so it survives restarts. Locally, without
``DATABASE_URL``, the bot keeps using the SQLite file as before.

The wrapper exposes the small ``execute``/``commit``/``close`` surface that
``app/database/requests.py`` relies on, so callers don't need to branch on
which engine is active.
"""
import sqlite3
import os
import logging

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional dependency for local SQLite-only use
    psycopg2 = None


class _PostgresConnection:
    """Adapts a psycopg2 connection to sqlite3.Connection's execute()/commit() surface.

    Neon (and similar serverless Postgres) suspends its compute after a few
    minutes of inactivity and drops the underlying TCP connection, so a
    long-lived connection can go stale between bot actions. Transparently
    reconnect once when that happens instead of surfacing an OperationalError.
    """

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._conn = psycopg2.connect(database_url)

    def _reconnect(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = psycopg2.connect(self._database_url)

    def execute(self, sql: str, params=()):
        pg_sql = sql.replace("?", "%s")
        try:
            cur = self._conn.cursor()
            cur.execute(pg_sql, params)
            return cur
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            self._reconnect()
            cur = self._conn.cursor()
            cur.execute(pg_sql, params)
            return cur

    def commit(self) -> None:
        try:
            self._conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            self._reconnect()

    def close(self) -> None:
        self._conn.close()


def connect(database_url: str | None, sqlite_path: str):
    """Return a connection to Postgres (if configured) or the local SQLite file.

    If DATABASE_URL is set but psycopg2 isn't installed, fall back to a local
    SQLite file and emit a warning so the process doesn't crash in non-prod envs.
    """
    if database_url:
        if psycopg2 is None:
            logging.warning(
                "DATABASE_URL is set but psycopg2 is not installed; falling back to "
                "SQLite at %s. To use Postgres, add psycopg2-binary to requirements.txt.",
                sqlite_path,
            )
            # fall back to sqlite
        else:
            return _PostgresConnection(database_url)

    conn = sqlite3.connect(sqlite_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
