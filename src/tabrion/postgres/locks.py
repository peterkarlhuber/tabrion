"""Session-level advisory locks (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` section 4.7 (TAB-030). Waiting polls ``pg_try_advisory_lock``: no statement is
active and no snapshot is open between attempts.

Why polling (Axentra ``migrations.py`` ``_wait_for_lock``): a blocking ``pg_advisory_lock`` keeps a statement, and
its snapshot, open while it waits. ``CREATE INDEX CONCURRENTLY`` in another session waits for every open snapshot,
so a migration process that holds the lock and runs such a statement, and a second process that waits blocked for
the lock, would deadlock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
import hashlib
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from tabrion.errors import UsageError
from tabrion.postgres._common import one_row, reading, require_connection, require_psycopg, settle

if TYPE_CHECKING:
    import psycopg

__all__ = ["SessionLock", "advisory_unlock", "lock_key", "try_advisory_lock", "wait_advisory_lock"]

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_LOCK_APPLICATION_NAME = "tabrion-lock"
_CONNECT_TIMEOUT = 10

_ABANDONED: list[Any] = []
"""Connections a forked child inherited: kept alive, with their socket pointed at ``/dev/null``, so that neither the
child's use nor its garbage collection can send anything on the parent's session (which would end its locks)."""


def lock_key(name: str) -> int:
    """A stable int64 key: the first 8 bytes of SHA-256(name), signed big-endian."""
    if not isinstance(name, str):
        raise TypeError("name must be a str")
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


def _key(key: object) -> int:
    if isinstance(key, bool) or not isinstance(key, int):
        raise TypeError("key must be an int")
    if not _INT64_MIN <= key <= _INT64_MAX:
        raise ValueError("key must fit in a signed 64-bit integer (see lock_key)")
    return int(key)


def _poll_interval(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("poll_interval must be a number of seconds")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("poll_interval must be a positive, finite number of seconds")
    return seconds


def _deadline(timeout: object) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError("timeout must be a number of seconds or None")
    seconds = float(timeout)
    if math.isnan(seconds) or seconds < 0:
        raise ValueError("timeout must be at least 0 seconds, or None")
    return time.monotonic() + seconds


def _sleep_until_next(deadline: float | None, poll_interval: float) -> bool:
    """Sleep one poll interval (or what is left of the deadline); ``False`` when the deadline has passed."""
    if deadline is None:
        time.sleep(poll_interval)
        return True
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(poll_interval, remaining))
    return True


def _detach_in_child(connection: Any) -> None:
    """In a forked child: make the inherited connection harmless without talking to the server (see ``_ABANDONED``)."""
    with suppress(Exception):
        fd = connection.pgconn.socket
        devnull = os.open(os.devnull, os.O_RDWR)
        try:
            os.dup2(devnull, fd)
        finally:
            os.close(devnull)
    _ABANDONED.append(connection)


class SessionLock:
    """Advisory locks on an own, unpooled autocommit connection (opened lazily, thread-safe, counted holds).

    A lost connection is replaced and every count is cleared (``holds()`` is then ``False``). After ``close()``
    every operation raises ``UsageError("postgres.lock_closed")``.

    The connection is ``psycopg.connect(conninfo, autocommit=True, connect_timeout=10, **connect_kwargs)`` (a
    ``connect_timeout`` in ``connect_kwargs`` wins; ``autocommit`` stays ``True``), configured by ``configure`` or
    by default with ``tabrion.postgres.configure(conn, SessionSettings(application_name="tabrion-lock"))``. Before
    every operation on held locks a ``SELECT 1`` checks that it is alive. Not fork-safe: a child process (another
    PID) leaves the parent's connection alone and opens its own, holding nothing.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        connect_kwargs: Mapping[str, Any] | None = None,
        configure: Callable[[psycopg.Connection[Any]], None] | None = None,
    ) -> None:
        require_psycopg()
        if not isinstance(conninfo, str):
            raise TypeError("conninfo must be a str")
        if connect_kwargs is not None and not isinstance(connect_kwargs, Mapping):
            raise TypeError("connect_kwargs must be a mapping")
        if configure is not None and not callable(configure):
            raise TypeError("configure must be callable")
        kwargs = dict(connect_kwargs or {})
        if "autocommit" in kwargs and kwargs.pop("autocommit") is not True:
            raise ValueError("the lock connection is always autocommit (session-level locks, no open snapshot)")
        kwargs.setdefault("connect_timeout", _CONNECT_TIMEOUT)
        self._conninfo = conninfo
        self._kwargs = kwargs
        self._configure = configure
        self._guard = threading.Lock()
        self._connection: psycopg.Connection[Any] | None = None
        self._held: dict[int, int] = {}
        self._pid = os.getpid()
        self._closed = False

    def __repr__(self) -> str:
        # Never the conninfo: it may hold a password.
        state = "closed" if self._closed else f"{len(self._held)} held"
        return f"<tabrion.postgres.SessionLock {state}>"

    # ---- the connection (always called under self._guard) ---------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise UsageError("postgres.lock_closed")
        if os.getpid() != self._pid:
            # A forked child: the connection and the counts belong to the parent.
            if self._connection is not None:
                _detach_in_child(self._connection)
            self._connection = None
            self._held.clear()
            self._pid = os.getpid()

    def _drop(self) -> None:
        """Forget a lost (or failing) connection: its locks are gone with it."""
        connection, self._connection = self._connection, None
        self._held.clear()
        if connection is not None:
            with suppress(Exception):
                connection.close()

    def _alive(self) -> psycopg.Connection[Any] | None:
        """The current connection after a ``SELECT 1``, or ``None`` (a dead one is dropped with its counts)."""
        import psycopg

        connection = self._connection
        if connection is None:
            return None
        try:
            connection.execute("SELECT 1")
        except psycopg.Error:
            self._drop()
            return None
        return connection

    def _open(self) -> psycopg.Connection[Any]:
        connection = self._alive()
        if connection is not None:
            return connection
        import psycopg

        connection = psycopg.connect(self._conninfo, autocommit=True, **self._kwargs)
        try:
            if self._configure is not None:
                self._configure(connection)
            else:
                from tabrion.postgres import SessionSettings, configure

                configure(connection, SessionSettings(application_name=_LOCK_APPLICATION_NAME))
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        return connection

    def _try(self, key: int) -> bool:
        import psycopg
        from psycopg.rows import tuple_row

        connection = self._open()
        if self._held.get(key):
            self._held[key] += 1
            return True
        try:
            with psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
                cursor.execute("SELECT pg_catalog.pg_try_advisory_lock(%s)", (key,))
                taken = bool(one_row(cursor)[0])
        except psycopg.Error:
            # Never repeated (R-NORETRY): the error passes through; a lost connection took every lock with it.
            if connection.closed or connection.broken:
                self._drop()
            raise
        if taken:
            self._held[key] = 1
        return taken

    # ---- the API ----------------------------------------------------------------------------------

    def try_acquire(self, key: int) -> bool:
        """``pg_try_advisory_lock(key)`` once; holding a key again only counts (no round trip besides the probe)."""
        key = _key(key)
        with self._guard:
            self._check_open()
            return self._try(key)

    def acquire(self, key: int, *, timeout: float | None = None, poll_interval: float = 0.2) -> bool:
        """Poll ``try_acquire`` every ``poll_interval`` seconds; ``False`` after ``timeout`` (``None``: no limit).

        The internal lock is released while sleeping, so other threads keep using this ``SessionLock``.
        """
        key = _key(key)
        interval = _poll_interval(poll_interval)
        deadline = _deadline(timeout)
        while True:
            with self._guard:
                self._check_open()
                if self._try(key):
                    return True
            if not _sleep_until_next(deadline, interval):
                return False

    def holds(self, key: int) -> bool:
        """Whether this process holds ``key`` on a live connection (``False`` after a lost connection)."""
        key = _key(key)
        with self._guard:
            self._check_open()
            if not self._held.get(key):
                return False
            self._alive()
            return bool(self._held.get(key))

    def release(self, key: int) -> None:
        """Give up one hold of ``key``; ``pg_advisory_unlock`` follows at the last one. Not held: nothing happens."""
        import psycopg
        from psycopg.rows import tuple_row

        key = _key(key)
        with self._guard:
            self._check_open()
            count = self._held.get(key, 0)
            if count > 1:
                self._held[key] = count - 1
                return
            if not count:
                return
            connection = self._alive()
            if connection is None:  # lost: the lock is gone already, and so are the counts
                return
            del self._held[key]
            try:
                with psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
                    cursor.execute("SELECT pg_catalog.pg_advisory_unlock(%s)", (key,))
            except psycopg.Error:
                # The connection failed while unlocking: dropping it ends the session, which frees every lock.
                self._drop()

    def held_keys(self) -> frozenset[int]:
        """The keys held on a live connection."""
        with self._guard:
            self._check_open()
            if self._held:
                self._alive()
            return frozenset(self._held)

    def close(self) -> None:
        """Close the connection, which frees every lock. Closing twice is harmless."""
        with self._guard:
            if self._closed:
                return
            if os.getpid() != self._pid:
                if self._connection is not None:
                    _detach_in_child(self._connection)
                self._connection = None
            self._closed = True
            self._drop()

    def __enter__(self) -> SessionLock:
        with self._guard:
            self._check_open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def try_advisory_lock(connection: psycopg.Connection[Any], key: int) -> bool:
    """``pg_try_advisory_lock`` on the given connection (Tabrion neither opens nor closes it).

    The lock is session-level: it outlives the implicit transaction that is rolled back on an IDLE connection
    without autocommit (T-NOTX). In a running transaction the statement runs under a savepoint; the session-level
    lock outlives it.
    """
    key = _key(key)
    with reading(connection, "try_advisory_lock") as cursor:
        cursor.execute("SELECT pg_catalog.pg_try_advisory_lock(%s)", (key,))
        return bool(one_row(cursor)[0])


def wait_advisory_lock(
    connection: psycopg.Connection[Any],
    key: int,
    *,
    poll_interval: float = 0.2,
    timeout: float | None = None,
) -> bool:
    """Poll ``pg_try_advisory_lock``; ``False`` after ``timeout``. ``UsageError("postgres.lock_in_transaction")``.

    Each attempt is one statement; without autocommit its implicit transaction is rolled back before the sleep, so
    between attempts no statement is active and no snapshot is open (compatible with ``CREATE INDEX CONCURRENTLY``
    in another session). A running transaction would hold its snapshot while waiting, so it is refused.
    """
    key = _key(key)
    interval = _poll_interval(poll_interval)
    deadline = _deadline(timeout)
    require_connection(connection, "wait_advisory_lock")
    from psycopg import pq

    # UNKNOWN (a closed or broken connection) is left to the driver, whose error passes through.
    in_transaction = (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR, pq.TransactionStatus.ACTIVE)
    while True:
        settle(connection)  # pipeline mode: ACTIVE until a sync, then the real status (review DL-1)
        if connection.info.transaction_status in in_transaction:
            raise UsageError("postgres.lock_in_transaction")
        if try_advisory_lock(connection, key):
            return True
        if not _sleep_until_next(deadline, interval):
            return False


def advisory_unlock(connection: psycopg.Connection[Any], key: int) -> bool:
    """The result of ``pg_advisory_unlock``."""
    key = _key(key)
    with reading(connection, "advisory_unlock") as cursor:
        cursor.execute("SELECT pg_catalog.pg_advisory_unlock(%s)", (key,))
        return bool(one_row(cursor)[0])
