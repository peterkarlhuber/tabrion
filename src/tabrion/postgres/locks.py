"""Session-level advisory locks (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` section 4.7. Stub for the API freeze G-T1; the implementation follows in TAB-030.
Waiting polls ``pg_try_advisory_lock``: no statement is active and no snapshot is open between attempts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

__all__ = ["SessionLock", "advisory_unlock", "lock_key", "try_advisory_lock", "wait_advisory_lock"]


def lock_key(name: str) -> int:
    """A stable int64 key: the first 8 bytes of SHA-256(name), signed big-endian."""
    raise NotImplementedError("tabrion.postgres.lock_key: implemented in TAB-030")


class SessionLock:
    """Advisory locks on an own, unpooled autocommit connection (opened lazily, thread-safe, counted holds).

    A lost connection is replaced and every count is cleared (``holds()`` is then ``False``). After ``close()``
    every operation raises ``UsageError("postgres.lock_closed")``.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        connect_kwargs: Mapping[str, Any] | None = None,
        configure: Callable[[psycopg.Connection[Any]], None] | None = None,
    ) -> None:
        raise NotImplementedError("tabrion.postgres.SessionLock: implemented in TAB-030")

    def try_acquire(self, key: int) -> bool:
        raise NotImplementedError("tabrion.postgres.SessionLock.try_acquire: implemented in TAB-030")

    def acquire(self, key: int, *, timeout: float | None = None, poll_interval: float = 0.2) -> bool:
        raise NotImplementedError("tabrion.postgres.SessionLock.acquire: implemented in TAB-030")

    def holds(self, key: int) -> bool:
        raise NotImplementedError("tabrion.postgres.SessionLock.holds: implemented in TAB-030")

    def release(self, key: int) -> None:
        raise NotImplementedError("tabrion.postgres.SessionLock.release: implemented in TAB-030")

    def held_keys(self) -> frozenset[int]:
        raise NotImplementedError("tabrion.postgres.SessionLock.held_keys: implemented in TAB-030")

    def close(self) -> None:
        raise NotImplementedError("tabrion.postgres.SessionLock.close: implemented in TAB-030")

    def __enter__(self) -> SessionLock:
        raise NotImplementedError("tabrion.postgres.SessionLock.__enter__: implemented in TAB-030")

    def __exit__(self, *exc: object) -> None:
        raise NotImplementedError("tabrion.postgres.SessionLock.__exit__: implemented in TAB-030")


def try_advisory_lock(connection: psycopg.Connection[Any], key: int) -> bool:
    """``pg_try_advisory_lock`` on the given connection (Tabrion neither opens nor closes it)."""
    raise NotImplementedError("tabrion.postgres.try_advisory_lock: implemented in TAB-030")


def wait_advisory_lock(
    connection: psycopg.Connection[Any],
    key: int,
    *,
    poll_interval: float = 0.2,
    timeout: float | None = None,
) -> bool:
    """Poll ``pg_try_advisory_lock``; ``False`` after ``timeout``. ``UsageError("postgres.lock_in_transaction")``."""
    raise NotImplementedError("tabrion.postgres.wait_advisory_lock: implemented in TAB-030")


def advisory_unlock(connection: psycopg.Connection[Any], key: int) -> bool:
    """The result of ``pg_advisory_unlock``."""
    raise NotImplementedError("tabrion.postgres.advisory_unlock: implemented in TAB-030")
