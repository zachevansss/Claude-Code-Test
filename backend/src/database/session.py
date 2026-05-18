"""Engine + session factory. SQLite-friendly defaults; swap DATABASE_URL for PG.

SQLite specifics applied here:
  * journal_mode=WAL  — readers and writers no longer block each other.
    Without this, a long-running dashboard read collides with the bot's tick
    writes and one of them dies with `database is locked` (took out the bot
    on 2026-05-17 and again 2026-05-18). WAL is persistent on the DB file, so
    setting it on every connect is a harmless no-op after the first time.
  * busy_timeout=30000 — if a writer briefly holds an exclusive lock during
    commit, other connections wait up to 30s instead of erroring instantly.
    This setting is per-connection and must be reapplied on every connect.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import settings

_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")  # safe + faster under WAL
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency — yields a Session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
