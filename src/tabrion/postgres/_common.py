"""Shared helpers of ``tabrion.postgres`` (private).

psycopg is imported inside the functions, never at module level: ``import tabrion.postgres`` works without the
extra ``postgres`` (layer B imports ``SessionSettings``; contract G-7), and a call without psycopg raises
``ImportError`` with the installation hint.

Every query of this package names catalog functions, relations, types and operators with the schema ``pg_catalog``
(``::pg_catalog.text``, ``OPERATOR(pg_catalog.=)``), so a schema the caller puts before ``pg_catalog`` in its
``search_path`` cannot replace them or run its code as the caller (contract I-16, review finding M-2). ``::int``
and ``::bool`` are safe as written: the grammar maps them to ``pg_catalog.int4`` and ``pg_catalog.bool``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

__all__ = [
    "INSTALL_HINT",
    "guarded",
    "one_row",
    "quote_ident",
    "reading",
    "require_connection",
    "require_psycopg",
    "settle",
]

INSTALL_HINT = (
    'tabrion.postgres needs the extra "postgres": '
    'pip install "tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
    " (Tabrion is not on PyPI: pin the commit you use)"
)


def require_psycopg() -> None:
    """Raise ``ImportError`` with the installation hint when psycopg is missing (or blocked)."""
    try:
        import psycopg  # noqa: F401
    except ImportError:
        raise ImportError(INSTALL_HINT) from None


def require_connection(connection: object, function: str) -> None:
    """``TypeError`` unless ``connection`` is a (synchronous) ``psycopg.Connection``."""
    require_psycopg()
    import psycopg

    if not isinstance(connection, psycopg.Connection):
        raise TypeError(f"tabrion.postgres.{function} needs a psycopg.Connection, not {type(connection).__name__}")


def settle(connection: psycopg.Connection[Any]) -> None:
    """In pipeline mode, sync the caller's pipeline first, so that the transaction status is known (review DL-1).

    Inside ``with connection.pipeline():`` the status is ``ACTIVE`` until the queued statements are synced; without
    the sync no savepoint would be opened and a failing statement of Tabrion's would abort the caller's pipeline and
    its transaction. ``Pipeline.sync()`` sends what the caller queued (it would be sent with Tabrion's statements
    anyway) and raises the error of a queued statement that failed: that error is the caller's and propagates.
    """
    from psycopg import pq

    if connection.pgconn.pipeline_status != pq.PipelineStatus.OFF:
        # The caller's pipeline, one level deeper. The explicit sync does not depend on psycopg also syncing when the
        # nested block ends (it does; the second sync then has nothing left to send).
        with connection.pipeline() as pipeline:
            pipeline.sync()


@contextmanager
def guarded(connection: psycopg.Connection[Any]) -> Iterator[None]:
    """A savepoint while the caller's transaction is running (INTRANS), else nothing (contract T-NOTX, I-16).

    A statement of Tabrion's that fails inside the caller's transaction would abort it (INERROR) and cost the caller
    its work; under the savepoint psycopg rolls back to it and the error propagates with the caller's transaction
    still usable. Success releases the savepoint (two more round trips). In pipeline mode the caller's pipeline is
    synced first (:func:`settle`), so the guard applies there too. An aborted transaction (INERROR) gets no
    savepoint: the statement fails with ``InFailedSqlTransaction`` as before (I-16).
    """
    from psycopg import pq

    settle(connection)
    if connection.info.transaction_status == pq.TransactionStatus.INTRANS:
        with connection.transaction():
            yield
    else:
        yield


@contextmanager
def reading(connection: psycopg.Connection[Any], function: str) -> Iterator[psycopg.Cursor[tuple[Any, ...]]]:
    """A ``tuple_row`` cursor for read-only statements on a handed-in connection (contract section 4, T-NOTX).

    The cursor is a plain ``psycopg.Cursor`` whatever the connection's row and cursor factories, which stay
    unchanged. Without autocommit and with no transaction open, the statements open an implicit transaction that is
    rolled back afterwards, also after an error, so the connection is IDLE again. In a running transaction the
    statements run under a savepoint (:func:`guarded`), so a failing one leaves the caller's transaction usable;
    nothing is committed or rolled back beyond that savepoint.
    """
    require_connection(connection, function)
    import psycopg
    from psycopg import pq
    from psycopg.rows import tuple_row

    settle(connection)
    own = not connection.autocommit and connection.info.transaction_status == pq.TransactionStatus.IDLE
    try:
        with guarded(connection), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
            yield cursor
    finally:
        # Only the implicit transaction these statements opened; a broken connection reports UNKNOWN and is left
        # alone, so the original error propagates.
        if own and connection.info.transaction_status in (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR):
            connection.rollback()


def one_row(cursor: psycopg.Cursor[tuple[Any, ...]]) -> tuple[Any, ...]:
    """The row of a statement that returns exactly one row by construction (a ``SELECT`` without ``FROM``)."""
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("tabrion.postgres: the statement returned no row")
    return row


def quote_ident(name: str) -> str:
    """``name`` as a quoted SQL identifier (``"`` doubled), as ``quote_ident`` and ``psycopg.sql.Identifier`` do."""
    return '"' + name.replace('"', '""') + '"'
