"""TAB-030 and TAB-034 without a server: ``lock_key``, argument validation, ``SessionLock`` repr and state, the
fault proxy's ``arm`` and ``dsn`` (the proxy itself runs against PostgreSQL in ``tests/postgres``)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing, suppress
import hashlib
import math
import os
import socket
import threading
import time
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


# ---- TB-143: the ``inflight`` cut against a fake server (deterministic, no PostgreSQL) -------------------------------


def _msg(kind: bytes, body: bytes) -> bytes:
    return kind + (4 + len(body)).to_bytes(4, "big") + body


STARTUP = (8).to_bytes(4, "big") + (196608).to_bytes(4, "big")  # protocol 3.0, no parameters
SQL = b"SELECT pg_catalog.pg_try_advisory_lock($1)\x00"
QUERY = _msg(b"Q", SQL)
PARSE = _msg(b"P", b"\x00" + SQL + b"\x00\x00")
REST = _msg(b"B", b"\x00\x00" + b"\x00\x00" * 3) + _msg(b"E", b"\x00" + b"\x00" * 4) + _msg(b"S", b"")
READY = _msg(b"Z", b"I")  # ReadyForQuery: the whole answer of the fake server
NOTICE = _msg(b"N", b"SWARNING\x00Mtabrion-notice\x00\x00")


def _until(condition: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Wait for something another thread signals (bounded; the outcome never depends on the time)."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.001)
    return True


class _Upstream:
    """A fake server: reads ``expected`` (or until the proxy closes its side) and answers ``READY`` at once."""

    def __init__(self, expected: bytes) -> None:
        self.expected = expected
        self.received = bytearray()
        self.answered = threading.Event()
        self.connected = threading.Event()
        self.connection: socket.socket | None = None
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        connection, _address = self.listener.accept()
        self.connection = connection
        self.connected.set()  # the proxy may already forward before this thread has accepted
        with connection:
            while len(self.received) < len(self.expected):
                data = connection.recv(65536)
                if not data:
                    return  # the proxy closed its side before the batch was complete
                self.received.extend(data)
            with suppress(OSError):
                connection.sendall(READY)
            self.answered.set()
            with suppress(OSError):
                connection.recv(1)  # until the proxy closes its side

    def close(self) -> None:
        self.thread.join(10)
        self.listener.close()


def _cut_in_flight(upstream: _Upstream, first: bytes, rest: bytes = b"", read: threading.Event | None = None) -> Any:
    """Send ``first`` (and ``rest`` once the fault fired) through an ``inflight`` proxy; the proxy and what the client
    read (``b""``: the cut, nothing else)."""
    from tabrion.testing._faults import FaultProxy

    # The client socket is closed after the proxy, whose close() joins its threads.
    with closing(socket.socket()) as client, FaultProxy("127.0.0.1", upstream.port) as proxy:
        client.connect(("127.0.0.1", proxy.port))
        client.settimeout(10)
        proxy.arm(b"pg_try_advisory_lock", fault="inflight")
        client.sendall(first)
        if rest:
            assert _until(lambda: proxy.fired == 1)
            client.sendall(rest)  # the batch arrives at the proxy in two reads
        try:
            seen = client.recv(65536)
        except ConnectionResetError:
            seen = b""
        if read is not None:
            read.set()
    upstream.close()
    return proxy, seen


def test_fault_proxy_inflight_lets_no_answer_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """TB-143: no answer passes an ``inflight`` cut, however late the proxy closes.

    Under CPU load the thread that forwards the batch could be descheduled right after sending it; the other thread
    then relayed the server's fast answer, and the client saw the statement succeed
    (``test_session_lock_raises_and_forgets[inflight]``: "DID NOT RAISE"; ``test_ping_reports_a_lost_connection``
    ``[inflight]``: ``ok=True``). Here the first ``_close`` (the forwarding thread's, after the batch went out) waits
    until the fake server has answered and that answer has either reached the client or been held back.
    """
    from tabrion.testing._faults import FaultProxy

    upstream = _Upstream(STARTUP + QUERY)
    read = threading.Event()
    late: list[str] = []
    close = FaultProxy._close

    def close_late(self: FaultProxy, link: Any) -> None:
        if not late:
            late.append(threading.current_thread().name)
            upstream.answered.wait(10)
            _until(lambda: read.is_set() or getattr(self, "muted_messages", 0) >= 1)
        close(self, link)

    monkeypatch.setattr(FaultProxy, "_close", close_late)
    proxy, seen = _cut_in_flight(upstream, STARTUP + QUERY, read=read)
    assert late and late[0] != threading.current_thread().name  # a proxy thread was held, not the test's
    assert upstream.answered.is_set() and bytes(upstream.received) == STARTUP + QUERY  # the batch reached the server
    assert seen == b""  # and its answer did not reach the client
    assert proxy.fired == 1


def test_fault_proxy_inflight_forwards_the_batch_when_the_server_writes_at_the_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review F-1 of #55: a server message at the moment of the cut (an asynchronous ``NoticeResponse``) neither
    reaches the client nor keeps the batch from the server (it must not turn ``inflight`` into ``request``)."""
    from tabrion.testing._faults import FaultProxy

    upstream = _Upstream(STARTUP + QUERY)
    mute = FaultProxy._mute

    def mute_then_notice(self: FaultProxy, link: Any) -> None:
        mute(self, link)
        assert upstream.connected.wait(10) and upstream.connection is not None
        upstream.connection.sendall(NOTICE)  # the server writes after the mute and before the batch goes out
        # Until the other thread has handled it: held back, or (the defect) relayed to a closed client, which closes
        # the server side too.
        _until(lambda: self.muted_messages >= 1 or link.server.fileno() == -1)

    monkeypatch.setattr(FaultProxy, "_mute", mute_then_notice)
    proxy, seen = _cut_in_flight(upstream, STARTUP + QUERY)
    assert upstream.answered.is_set() and bytes(upstream.received) == STARTUP + QUERY
    assert seen == b""
    assert proxy.fired == 1 and proxy.muted_messages >= 1


def test_fault_proxy_inflight_forwards_a_batch_split_across_reads() -> None:
    """Review F-3 of #55: the cut forwards the batch through its ``Sync``, also when the rest arrives later."""
    upstream = _Upstream(STARTUP + PARSE + REST)
    proxy, seen = _cut_in_flight(upstream, STARTUP + PARSE, REST)
    assert upstream.answered.is_set() and bytes(upstream.received) == STARTUP + PARSE + REST
    assert seen == b""
    assert proxy.fired == 1


def test_fault_proxy_is_not_public() -> None:
    """TB-033 (decided): the proxy is internal; ``tabrion.testing`` does not export it and surface.json omits it."""
    import json
    from pathlib import Path

    import tabrion.testing

    assert not hasattr(tabrion.testing, "FaultProxy") and "faults" not in tabrion.testing.__all__
    surface = (Path(__file__).parents[2] / "docs" / "api" / "surface.json").read_text(encoding="utf-8")
    assert "_faults" not in surface and "FaultProxy" not in surface
    json.loads(surface)


def test_lock_key_unpaired_surrogate() -> None:
    """Review Info I-2 (#6): the documented ``ValueError``, without the value."""
    with pytest.raises(ValueError, match="UTF-8") as info:
        lock_key("tabrion-\ud800")
    assert type(info.value) is ValueError
    assert "\ud800" not in str(info.value)


class _Lying(int):
    """An int subclass whose comparisons and conversions lie (review Info I-1 of #6)."""

    def __le__(self, other: object) -> bool:
        return True

    def __ge__(self, other: object) -> bool:
        return True

    def __int__(self) -> int:
        return 2**70

    def __index__(self) -> int:
        return 2**70


def test_keys_are_checked_by_their_exact_value() -> None:
    from tabrion.postgres.locks import _key

    assert _key(_Lying(5)) == 5 and type(_key(_Lying(5))) is int
    with pytest.raises(ValueError, match="64-bit"):
        _key(_Lying(2**63))


def test_session_lock_connects_with_the_documented_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review L-4 (#6): ``autocommit=True`` and ``connect_timeout=10`` unless ``connect_kwargs`` names its own."""
    seen: list[tuple[str, dict[str, Any]]] = []

    class Stop(Exception):
        pass

    def fake_connect(conninfo: str, **kwargs: Any) -> Any:
        seen.append((conninfo, kwargs))
        raise Stop

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    for kwargs, timeout in (({}, 10), ({"connect_timeout": 3, "keepalives_idle": 30}, 3)):
        with pytest.raises(Stop):
            SessionLock("dbname=x", connect_kwargs=kwargs).try_acquire(1)
        conninfo, used = seen[-1]
        assert conninfo == "dbname=x"
        assert used["autocommit"] is True and used["connect_timeout"] == timeout
        assert {k: v for k, v in used.items() if k not in ("autocommit", "connect_timeout")} == {
            k: v for k, v in kwargs.items() if k != "connect_timeout"
        }


@pytest.mark.skipif(not hasattr(os, "register_at_fork"), reason="POSIX only")
def test_fork_hook_replaces_the_internal_lock() -> None:
    """Review L-2 (#6): the hook that runs in a forked child gives every live SessionLock a new internal lock."""
    from tabrion.postgres import locks

    lock = SessionLock("dbname=x")
    held = lock._guard
    held.acquire()
    try:
        locks._after_fork_in_child()
        assert lock._guard is not held and lock._guard.acquire(blocking=False)
        lock._guard.release()
    finally:
        held.release()
