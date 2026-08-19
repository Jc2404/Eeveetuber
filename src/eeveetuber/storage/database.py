"""SQLite engine initialization, durability pragmas, and optional FTS5 setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from eeveetuber.storage.models import Base


@dataclass(frozen=True, slots=True)
class DatabaseFeatures:
    fts5: bool
    journal_mode: str


class SqliteDatabase:
    """Own an SQLite engine and schema lifecycle.

    File databases use WAL and NORMAL synchronous mode.  Tests may request an
    in-memory database, which uses one shared connection because SQLite scopes an
    in-memory database to a connection.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        echo: bool = False,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        raw_path = str(path)
        self.path = raw_path
        self._is_memory = raw_path == ":memory:"
        url = "sqlite+pysqlite:///:memory:" if self._is_memory else _sqlite_file_url(Path(path))
        engine_kwargs: dict[str, object] = {
            "echo": echo,
            "future": True,
            "connect_args": {"check_same_thread": False},
        }
        if self._is_memory:
            engine_kwargs["poolclass"] = StaticPool
        else:
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **engine_kwargs)
        self._busy_timeout_ms = busy_timeout_ms
        _install_sqlite_pragmas(
            self.engine,
            busy_timeout_ms=busy_timeout_ms,
            enable_wal=not self._is_memory,
        )
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )
        self.features = DatabaseFeatures(fts5=False, journal_mode="unknown")

    def initialize(self) -> DatabaseFeatures:
        Base.metadata.create_all(self.engine)
        fts5 = False
        with self.engine.begin() as connection:
            journal_mode = str(connection.execute(text("PRAGMA journal_mode")).scalar_one()).lower()
            try:
                connection.execute(
                    text(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                        "USING fts5(message_id UNINDEXED, session_id UNINDEXED, content, "
                        "tokenize='unicode61')"
                    )
                )
                connection.execute(
                    text(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                        "USING fts5(revision_id UNINDEXED, memory_id UNINDEXED, "
                        "namespace UNINDEXED, subject, content, tokenize='unicode61')"
                    )
                )
                fts5 = True
            except Exception as error:
                # SQLite builds without FTS5 still support the canonical stores.
                if "fts5" not in str(error).lower():
                    raise
        self.features = DatabaseFeatures(fts5=fts5, journal_mode=journal_mode)
        return self.features

    def close(self) -> None:
        self.engine.dispose()


def _sqlite_file_url(path: Path) -> str:
    absolute = path.expanduser().resolve().as_posix()
    return f"sqlite+pysqlite:///{absolute}"


def _install_sqlite_pragmas(
    engine: Engine,
    *,
    busy_timeout_ms: int,
    enable_wal: bool,
) -> None:
    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            if enable_wal:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

