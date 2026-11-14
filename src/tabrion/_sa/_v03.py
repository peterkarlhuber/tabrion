"""The 0.3 method of ``Transaction`` (TAB-XW-6, guided write transaction) as a mixin. Private: use ``tabrion``.

``Transaction`` in ``tabrion._sa.database`` inherits from :class:`_TransactionV03` only when the package version is
0.3 or later (``tabrion._since``); before that, ``execute_checked`` is staged here, off the public class, as the 0.2
methods were before 0.2 (G-T1 condition C-6, ADR 0015 Consequences). Contract section 5.11, implementation note I-120.

Like ``tabrion._sa._v02`` this module reads ``tabrion._sa.database`` as a module when a method runs, and imports it at
its end, so ``database`` imports the mixin at its top without an import cycle that fails (TB-024).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy

from tabrion._sa._types import Statement
from tabrion.errors import Kind, ReadOnlyViolation, TabrionError, UsageError, _RowCountMismatch, classify

if TYPE_CHECKING:
    from tabrion._sa.database import Transaction

__all__: list[str] = []

_PRESERVE_ROWCOUNT = {"preserve_rowcount": True}
"""SQLAlchemy 2.0.28 and later: the driver's row count for every statement kind, also an ``INSERT`` (whose cursor
SQLAlchemy closes before the count is read otherwise, so it reports ``-1``)."""


def _limit(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _rowcount(result: sqlalchemy.CursorResult[Any]) -> int:
    """The driver's row count as reported, ``-1`` (unknown) included; never clamped (I-120 c)."""
    count = result.rowcount
    result.close()
    return count


def _count(transaction: Transaction, statement: Statement, values: dict[str, Any] | None, timeout: float | None) -> int:
    """Run one command (PostgreSQL's extended protocol, I-120 d) under the transaction's rules; its row count."""
    try:
        executable = _db._statement(statement)
        _v02._refuse_transaction_control(transaction, executable)
    except BaseException:
        del statement, values  # I-76
        raise
    connection = transaction._connection

    def run() -> int:
        with _v02._extended_protocol(transaction):
            result = connection.execute(executable, values, execution_options=_PRESERVE_ROWCOUNT)
        return _rowcount(result)

    try:
        count = transaction._step(statement, timeout, run)
        _v02._still_in_transaction(transaction)
    except BaseException:
        # ``values`` and ``executable`` are cells of ``run``: deleting them empties the cells (I-76).
        del statement, values, executable
        raise
    return count


def _read_only_error() -> ReadOnlyViolation:
    """``ReadOnlyViolation`` for a write refused in a ``read()`` transaction before anything was sent (I-120 b)."""
    classification = replace(classify(Exception(), sent=False), kind=Kind.READ_ONLY, connection_usable=True)
    return ReadOnlyViolation("database.read_only", classification=classification)


def _mismatch(count: int, expected: int | None, limit: int | None) -> TabrionError | None:
    """The ``RowCountMismatch`` for ``count`` against the limits, or ``None``; details are counts only (I-120 c)."""
    if count < 0:
        return _RowCountMismatch("transaction.rowcount_mismatch", details={"expected": expected, "actual": count})
    if limit is not None and count > limit:
        return _RowCountMismatch("transaction.too_many_rows", details={"limit": limit, "actual": count})
    if expected is not None and count != expected:
        return _RowCountMismatch("transaction.rowcount_mismatch", details={"expected": expected, "actual": count})
    return None


class _TransactionV03:
    """Mixed into ``Transaction`` from 0.3 on (``tabrion._since``), before the 0.2 mixin."""

    def execute_checked(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        expect_rowcount: int | None = None,
        max_affected: int | None = None,
        timeout: float | None = None,
    ) -> int:
        """Since 0.3, experimental. One statement whose affected row count must be ``expect_rowcount`` and at most
        ``max_affected`` (one of the two is required); otherwise ``RowCountMismatch``, and the transaction is failed:
        every later statement raises ``UsageError("transaction.failed")`` and the block rolls back (I-120).

        The count is that of the statement itself only (the driver's ``rowcount``): rows that rules or triggers change
        as a side effect are not counted and not limited, so a preview must name the rules and triggers of the target
        table (Studio TX-16; I-120 g). ``max_affected`` limits what is committed, not the work: the limits are checked
        after the statement ran, so the bound on the work (time and locks) is ``timeout`` (I-120 h)."""
        transaction = _tx(self)
        values: dict[str, Any] | None = None
        try:
            expected = _limit(expect_rowcount, "expect_rowcount")
            limit = _limit(max_affected, "max_affected")
            values = None if params is None else _db._mapping(params)
            transaction._check()
            if expected is None and limit is None:
                raise UsageError("transaction.limit_required")
            if transaction._read_only:
                raise _read_only_error()
            count = _count(transaction, statement, values, timeout)
        except BaseException:
            del statement, params, values  # no values or SQL text in the frames of the traceback (I-76)
            raise
        del statement, params, values  # I-76: no values in the frame of the error below
        error = _mismatch(count, expected, limit)
        if error is not None:
            transaction._fail(error)
            raise error
        return count


def _tx(obj: object) -> Transaction:
    return cast("Transaction", obj)


# At the end, not the top: ``tabrion._sa.database`` imports this mixin at its top, so whichever module is imported
# first, the other finds what it needs (TB-024, as ``_v02``). The method reads ``_v02`` and ``_db`` only when it runs.
from tabrion._sa import _v02  # noqa: E402
from tabrion._sa import database as _db  # noqa: E402
