"""The 0.2 methods of ``Database`` and ``Transaction`` (TAB-033), staged for the API freeze. Private.

G-T1 condition C-6: the 0.1.0 surface must not export a 0.2 name. These mixins hold the frozen 0.2 signatures, so
``scripts/api_surface.py`` can compare them with ``docs/api/surface.json``, but ``Database`` and ``Transaction``
inherit from them only when the package version reaches 0.2 (TAB-033). Contract sections 5.3 to 5.6.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

import sqlalchemy

from tabrion._sa.database import Isolation, Statement, Transaction
from tabrion._sa.result import Result

__all__: list[str] = []

T = TypeVar("T")


class _TransactionV02:
    """Mixed into ``Transaction`` in 0.2."""

    def query(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Since 0.2."""
        raise NotImplementedError("tabrion.Transaction.query: implemented in TAB-033")

    def stream(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        batch_size: int = 500,
        timeout: float | None = None,
    ) -> Iterator[Sequence[sqlalchemy.Row[Any]]]:
        """Since 0.2. PostgreSQL: server-side cursor; SQLite: ``fetchmany``."""
        raise NotImplementedError("tabrion.Transaction.stream: implemented in TAB-033")

    def cancel(self) -> None:
        """Since 0.2. Callable from another thread."""
        raise NotImplementedError("tabrion.Transaction.cancel: implemented in TAB-033")

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Since 0.2, experimental."""
        raise NotImplementedError("tabrion.Transaction.savepoint: implemented in TAB-033")


class _DatabaseV02:
    """Mixed into ``Database`` in 0.2."""

    def query(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Since 0.2. In its own ``read()``."""
        raise NotImplementedError("tabrion.Database.query: implemented in TAB-033")

    @contextmanager
    def inspector(self) -> Iterator[sqlalchemy.Inspector]:
        """Since 0.2. On a read-only connection."""
        raise NotImplementedError("tabrion.Database.inspector: implemented in TAB-033")

    def on_begin(self, callback: Callable[[sqlalchemy.Connection, bool], None]) -> Callable[[], None]:
        """Since 0.2, experimental. ``bool`` is ``read_only``; returns the function that unregisters it."""
        raise NotImplementedError("tabrion.Database.on_begin: implemented in TAB-033")

    def run_transaction(
        self,
        fn: Callable[[Transaction], T],
        *,
        attempts: int = 3,
        isolation: Isolation | None = None,
        backoff: float = 0.05,
    ) -> T:
        """Since 0.2, experimental. Retries only ``retryable and not outcome_unknown``."""
        raise NotImplementedError("tabrion.Database.run_transaction: implemented in TAB-033")
