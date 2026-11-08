"""TAB-030 and TAB-034 without a server: ``lock_key``, argument validation, ``SessionLock`` repr and state, the
fault proxy's ``arm`` and ``dsn`` (the proxy itself runs against PostgreSQL in ``tests/postgres``)."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import math
from typing import Any

import pytest

from tabrion.errors import UsageError
from tabrion.postgres import SessionLock, advisory_unlock, lock_key, try_advisory_lock, wait_advisory_lock

psycopg = pytest.importorskip("psycopg")

CANARY = "SECRET-4711"


def test_lock_key_is_stable_and_signed() -> None:
    expected = int.from_bytes(hashlib.sha256(b"tabrion").digest()[:8], "big", signed=True)
    assert lock_key("tabrion") == expected
    assert lock_key("tabrion") != lock_key("tabrion ")
    keys = {lock_key(f"name-{i}") for i in range(2_000)}
    assert len(keys) == 2_000
    assert all(-(2**63) <= k < 2**63 for k in keys)
    assert any(k < 0 for k in keys) and any(k > 0 for k in keys)
    assert lock_key("ä") == int.from_bytes(hashlib.sha256("ä".encode()).digest()[:8], "big", signed=True)


@pytest.mark.parametrize("name", [b"tabrion", 1, None])
def test_lock_key_needs_a_str(name: object) -> None:
    with pytest.raises(TypeError):
        lock_key(name)  # type: ignore[arg-type]


KEY_CALLS: dict[str, Callable[[Any], object]] = {
    "try_advisory_lock": lambda k: try_advisory_lock(None, k),  # type: ignore[arg-type]
    "advisory_unlock": lambda k: advisory_unlock(None, k),  # type: ignore[arg-type]
    "wait_advisory_lock": lambda k: wait_advisory_lock(None, k),  # type: ignore[arg-type]
    "SessionLock.try_acquire": lambda k: SessionLock("dbname=x").try_acquire(k),
    "SessionLock.acquire": lambda k: SessionLock("dbname=x").acquire(k),
}


@pytest.mark.parametrize("call", list(KEY_CALLS))
@pytest.mark.parametrize(
    ("key", "error"),
    [(True, TypeError), (1.0, TypeError), ("1", TypeError), (2**63, ValueError), (-(2**63) - 1, ValueError)],
)
def test_keys_are_validated_before_the_connection(call: str, key: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        KEY_CALLS[call](key)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"poll_interval": 0}, ValueError),
        ({"poll_interval": -1}, ValueError),
        ({"poll_interval": math.inf}, ValueError),
        ({"poll_interval": True}, TypeError),
        ({"timeout": -0.1}, ValueError),
        ({"timeout": math.nan}, ValueError),
        ({"timeout": "1"}, TypeError),
    ],
)
def test_wait_arguments(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        wait_advisory_lock(None, 1, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(error):
        SessionLock("dbname=x").acquire(1, **kwargs)  # type: ignore[arg-type]


def test_helpers_need_a_psycopg_connection() -> None:
    with pytest.raises(TypeError, match=r"psycopg\.Connection"):
        try_advisory_lock(object(), 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"psycopg\.Connection"):
        advisory_unlock(object(), 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"psycopg\.Connection"):
        wait_advisory_lock(object(), 1)  # type: ignore[arg-type]


def test_session_lock_arguments() -> None:
    with pytest.raises(TypeError):
        SessionLock(b"dbname=x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SessionLock("dbname=x", connect_kwargs=[("a", 1)])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SessionLock("dbname=x", configure="no")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="autocommit"):
        SessionLock("dbname=x", connect_kwargs={"autocommit": False})
    SessionLock("dbname=x", connect_kwargs={"autocommit": True})  # the one allowed value


def test_session_lock_repr_and_state_without_a_server() -> None:
    lock = SessionLock(f"host=127.0.0.1 password={CANARY} dbname=x")
    assert CANARY not in repr(lock) and "password" not in repr(lock)
    assert repr(lock) == "<tabrion.postgres.SessionLock 0 held>"
    assert lock.held_keys() == frozenset()
    assert lock.holds(1) is False
    lock.release(1)  # not held: nothing happens, nothing connects
    lock.close()
    lock.close()  # idempotent
    assert repr(lock) == "<tabrion.postgres.SessionLock closed>"
    with pytest.raises(UsageError) as closed:
        lock.try_acquire(1)
    assert closed.value.code == "postgres.lock_closed"


def test_fault_proxy_arm_and_dsn() -> None:
    from tabrion.testing._faults import FAULTS, FaultProxy

    assert FAULTS == ("reply", "inflight", "request", "delay")
    with FaultProxy("127.0.0.1", 9) as proxy:  # nothing connects: the upstream is never dialled
        with pytest.raises(ValueError, match="fault"):
            proxy.arm(b"x", fault="drop")
        with pytest.raises(TypeError):
            proxy.arm("INSERT", fault="reply")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            proxy.arm(b"")
        with pytest.raises(ValueError, match="delay"):
            proxy.arm(b"x", fault="delay", delay=-1)
        assert not proxy.armed
        proxy.arm(b"x")
        assert proxy.armed and proxy.fired == 0
        dsn = psycopg.conninfo.conninfo_to_dict(proxy.dsn("hostaddr=10.0.0.1 host=db port=6543 dbname=app user=u"))
        assert dsn["host"] == "127.0.0.1" and dsn["port"] == str(proxy.port) and "hostaddr" not in dsn
        assert (dsn["sslmode"], dsn["gssencmode"], dsn["dbname"], dsn["user"]) == ("disable", "disable", "app", "u")
        proxy.check()  # nothing raised
    proxy.close()  # idempotent


def test_fault_proxy_is_not_public() -> None:
    """TB-033 (decided): the proxy is internal; ``tabrion.testing`` does not export it and surface.json omits it."""
    import json
    from pathlib import Path

    import tabrion.testing

    assert not hasattr(tabrion.testing, "FaultProxy") and "faults" not in tabrion.testing.__all__
    surface = (Path(__file__).parents[2] / "docs" / "api" / "surface.json").read_text(encoding="utf-8")
    assert "_faults" not in surface and "FaultProxy" not in surface
    json.loads(surface)
