"""``Database`` and ``Transaction``: layer B over a SQLAlchemy engine. Private: use ``tabrion.Database``.

Contract: ``docs/api/contract.md`` sections 5.2, 5.3, 5.5 and 5.6. Stub for the API freeze G-T1; the
implementation follows in TAB-018. The 0.2 methods (TAB-033) are staged in ``tabrion._sa._v02`` and are not part
of these classes before 0.2 (G-T1 condition C-6, ``docs/api/surface.json``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

import sqlalchemy

from tabrion._sa.profile import Profile
from tabrion.errors import Classification

if TYPE_CHECKING:
    from tabrion.check import Finding

__all__ = ["Database", "Health", "Params", "Statement", "Transaction"]

Statement: TypeAlias = str | sqlalchemy.sql.Executable
"""A ``str`` becomes ``sqlalchemy.text()`` with named parameters (``:name``)."""
Params: TypeAlias = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None
"""A sequence of mappings runs ``executemany``."""
Isolation: TypeAlias = Literal["read_committed", "repeatable_read", "serializable"]


@dataclass(frozen=True, slots=True)
class Health:
    ok: bool
    dialect: str
    latency_ms: float | None
    server_version: str | None
    pool: Mapping[str, int] | None
    """``checked_out``, ``size``, ``overflow`` (SQLAlchemy pool status)."""
    classification: Classification | None


class Transaction:
    """One Tabrion transaction; bound to the opening thread (only ``cancel`` may come from another thread)."""

    @property
    def read_only(self) -> bool:
        raise NotImplementedError("tabrion.Transaction.read_only: implemented in TAB-018")

    @property
    def connection(self) -> sqlalchemy.Connection:
        """Controlled access to the underlying SQLAlchemy connection."""
        raise NotImplementedError("tabrion.Transaction.connection: implemented in TAB-018")

    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int:
        """Row count (``-1`` becomes ``0`` for DDL)."""
        raise NotImplementedError("tabrion.Transaction.execute: implemented in TAB-018")

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """0 rows: ``None``; more than one: ``MultipleRowsError``."""
        raise NotImplementedError("tabrion.Transaction.fetch_one: implemented in TAB-018")

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        raise NotImplementedError("tabrion.Transaction.fetch_all: implemented in TAB-018")

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """First column of the only row; 0 rows: ``None``; more than one: ``MultipleRowsError``."""
        raise NotImplementedError("tabrion.Transaction.fetch_scalar: implemented in TAB-018")


class Database:
    """A thread-safe handle on a SQLAlchemy engine with Tabrion's profiles and transaction modes."""

    @classmethod
    def from_url(
        cls,
        url: str | sqlalchemy.URL,
        *,
        profile: Profile | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> Database:
        """``sqlite:///path``, ``sqlite://`` (memory), ``postgresql://`` (rewritten to ``+psycopg``)."""
        raise NotImplementedError("tabrion.Database.from_url: implemented in TAB-018")

    @classmethod
    def from_env(
        cls,
        name: str = "DATABASE_URL",
        *,
        profile: Profile | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> Database:
        """Through ``tabrion.config.url_from_env`` (supports ``*_FILE``)."""
        raise NotImplementedError("tabrion.Database.from_env: implemented in TAB-018")

    @classmethod
    def from_engine(
        cls, engine: sqlalchemy.Engine, *, profile: Profile | None = None, install_listeners: bool = True
    ) -> Database:
        """A foreign engine; ``close()`` does not dispose it."""
        raise NotImplementedError("tabrion.Database.from_engine: implemented in TAB-018")

    @property
    def dialect(self) -> Literal["sqlite", "postgresql"]:
        raise NotImplementedError("tabrion.Database.dialect: implemented in TAB-018")

    @property
    def engine(self) -> sqlalchemy.Engine:
        raise NotImplementedError("tabrion.Database.engine: implemented in TAB-018")

    @property
    def safe_url(self) -> str:
        raise NotImplementedError("tabrion.Database.safe_url: implemented in TAB-018")

    @property
    def closed(self) -> bool:
        raise NotImplementedError("tabrion.Database.closed: implemented in TAB-018")

    def close(self) -> None:
        """``engine.dispose()``; idempotent."""
        raise NotImplementedError("tabrion.Database.close: implemented in TAB-018")

    def __enter__(self) -> Database:
        raise NotImplementedError("tabrion.Database.__enter__: implemented in TAB-018")

    def __exit__(self, *exc: object) -> None:
        raise NotImplementedError("tabrion.Database.__exit__: implemented in TAB-018")

    @contextmanager
    def transaction(self, *, timeout: float | None = None, isolation: Isolation | None = None) -> Iterator[Transaction]:
        """SQLite ``BEGIN IMMEDIATE``; PostgreSQL ``BEGIN [ISOLATION LEVEL ...]``. Nested: ``UsageError``."""
        raise NotImplementedError("tabrion.Database.transaction: implemented in TAB-018")

    @contextmanager
    def read(self, *, timeout: float | None = None) -> Iterator[Transaction]:
        """SQLite ``query_only`` + ``BEGIN``; PostgreSQL ``BEGIN READ ONLY``."""
        raise NotImplementedError("tabrion.Database.read: implemented in TAB-018")

    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int:
        """In its own ``transaction()``."""
        raise NotImplementedError("tabrion.Database.execute: implemented in TAB-018")

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """In its own ``read()``."""
        raise NotImplementedError("tabrion.Database.fetch_one: implemented in TAB-018")

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        """In its own ``read()``."""
        raise NotImplementedError("tabrion.Database.fetch_all: implemented in TAB-018")

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """In its own ``read()``."""
        raise NotImplementedError("tabrion.Database.fetch_scalar: implemented in TAB-018")

    def health(self, *, timeout: float = 5.0, deep: bool = False) -> Health:
        """Never raises for database errors. ``deep=True`` adds ``PRAGMA quick_check(1)`` on SQLite (G-9)."""
        raise NotImplementedError("tabrion.Database.health: implemented in TAB-018")

    def check(self) -> list[Finding]:
        """``tabrion.check.check(self)``."""
        raise NotImplementedError("tabrion.Database.check: implemented in TAB-018")
