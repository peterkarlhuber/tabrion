"""Fault-injection proxy for PostgreSQL tests (TAB-034, QA-3). **Internal and unstable.**

This module is private (underscore name): it is not part of the frozen API surface (``docs/api/surface.json``), may
change in any release and is used by Tabrion's own tests (TAB-T-170, TAB-T-E14). Whether it becomes the public
``tabrion.testing.faults`` is a 0.2 surface decision by contract addendum and review (TODO TB-033).

:class:`FaultProxy` is a TCP proxy in front of a PostgreSQL server that follows the protocol's message framing and
injects one fault into the traffic of one statement. Derived from the probe ``jobqueue_retry_probe.py`` (planning
document 07, K7) and the ``FaultProxy`` fixture of nodestro-runtime (JQ-1). The cut is placed by the protocol,
never by a timer, so the tests are reproducible: nothing here polls, except :func:`wait_backend_gone`.

Standard library only; :meth:`FaultProxy.dsn` and :func:`wait_backend_gone` import psycopg when called.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
import socket
import threading
import time

__all__ = ["FAULTS", "FaultProxy", "wait_backend_gone"]

FAULTS = ("reply", "inflight", "request", "delay")
"""The faults :meth:`FaultProxy.arm` can inject (see the class documentation)."""

_BATCH_END = (b"Q", b"S", b"H")
"""Client messages that end a batch: a simple ``Query``, a ``Sync`` or a ``Flush`` (the server answers after each)."""


class _Link:
    """One proxied connection: both sockets, its fault state, its server process and the ``then`` of a fault."""

    def __init__(self, client: socket.socket, server: socket.socket) -> None:
        self.client = client
        self.server = server
        self.dropping = threading.Event()
        self.muted = threading.Event()
        self.delay_until: float | None = None
        self.backend: int | None = None
        self.then: Callable[[], None] | None = None


class FaultProxy:
    """A TCP proxy that injects one fault into the traffic of one statement.

    :meth:`arm` names a statement by a part of its SQL text, as the client sends it (psycopg turns ``%s`` into
    ``$1``, ``$2`` ...). The next client message that carries it (a simple ``Query`` or the ``Parse`` of the
    extended protocol) fires the fault once, on that connection only:

    - ``"reply"``: the statement reaches the server, which executes it (and, outside a transaction, commits it);
      every server message from then on, up to and including the ``ReadyForQuery`` that ends it, is dropped, then
      both sides are closed. The client sees a lost connection after its statement ended (outcome unknown).
    - ``"inflight"``: the connection is muted (nothing the server sends from then on reaches the client), the
      statement and the rest of its batch, through the next ``Sync`` or ``Flush`` (a simple ``Query`` is its own
      batch), are forwarded to the server, also when the batch arrives in several reads, and then both sides are
      closed. So the batch always reaches the server, and no answer to it reaches the client, however the proxy's
      threads are scheduled and whatever else the server sends meanwhile. The server runs what it received; its
      process ends when it next writes to the closed socket. A client that sends neither ``Sync`` nor ``Flush``
      after the statement keeps the cut waiting until it closes its side.
    - ``"request"``: the message is never forwarded; both sides are closed. The server never saw the statement.
    - ``"delay"``: the server's answer to the statement is held back for ``delay`` seconds, then forwarded; the
      connection stays usable.

    ``then`` runs once in the proxy: after the server ended the statement and before the client sees the cut
    (``reply``), right after the cut (``inflight``, ``request``) or before the delayed answer goes out (``delay``).
    What it raises is kept for :meth:`check`. :attr:`fired_backend` is the server process of the connection the
    fault fired on (from its ``BackendKeyData``). Every other connection and statement passes through unchanged.

    Connect only through :meth:`dsn`: it disables TLS and GSS encryption, whose negotiation (``SSLRequest`` and the
    server's one-byte answer) the framing does not follow. With ``reply``, server messages of earlier statements in
    the same batch that were not relayed yet are dropped too.
    """

    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self._upstream = (upstream_host, upstream_port)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._listener.getsockname()[1]
        self._lock = threading.Lock()
        self._armed: tuple[bytes, str, float, Callable[[], None] | None] | None = None
        self.fired = 0
        """How often a fault fired."""
        self.replies_lost = 0
        """How often a lost reply was cut after the server ended the statement."""
        self.muted_messages = 0
        """How many server messages an ``inflight`` fault kept from the client."""
        self.fired_backend: int | None = None
        self.error: BaseException | None = None
        """What ``then`` raised (re-raised by :meth:`check`)."""
        self._closing = False
        self._open: set[socket.socket] = set()
        self._threads: list[threading.Thread] = []
        self._accepting = threading.Thread(target=self._accept, name="tabrion-fault-proxy-accept", daemon=True)
        self._accepting.start()

    @classmethod
    def in_front_of(cls, conninfo: str) -> FaultProxy:
        """A proxy in front of the server of ``conninfo`` (``hostaddr`` or ``host``, default ``127.0.0.1:5432``)."""
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(conninfo)
        host = str(parts.get("hostaddr") or parts.get("host") or "127.0.0.1")
        return cls(host, int(parts.get("port") or 5432))

    def dsn(self, base: str) -> str:
        """``base`` with this proxy as its server (no TLS, no GSS encryption: the proxy reads the plain protocol)."""
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        parts = conninfo_to_dict(base)
        parts.pop("hostaddr", None)
        parts.update(host="127.0.0.1", port=str(self.port), sslmode="disable", gssencmode="disable")
        return str(make_conninfo("", **parts))

    def arm(
        self, statement: bytes, *, fault: str = "reply", delay: float = 0.0, then: Callable[[], None] | None = None
    ) -> None:
        """Fire ``fault`` once on the next client message whose SQL contains ``statement``."""
        if fault not in FAULTS:
            raise ValueError(f"fault must be one of {FAULTS}")
        if not isinstance(statement, bytes) or not statement:
            raise TypeError("statement must be non-empty bytes")
        if delay < 0:
            raise ValueError("delay must be at least 0 seconds")
        with self._lock:
            self._armed = (statement, fault, float(delay), then)

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed is not None

    def check(self) -> None:
        """Re-raise what ``then`` raised."""
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        with self._lock:
            self._closing = True
            sockets = list(self._open)
        # Wake the blocked accept() portably: a connection to ourselves, which the accept loop sees and drops.
        with suppress(OSError):
            socket.create_connection(("127.0.0.1", self.port)).close()
        self._accepting.join(5)
        self._listener.close()
        for sock in sockets:
            _cut(sock)
        for thread in self._threads:
            thread.join(5)

    def __enter__(self) -> FaultProxy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- the proxy -------------------------------------------------------------------------------

    def _accept(self) -> None:
        while True:
            try:
                client, _address = self._listener.accept()
            except OSError:
                return
            with self._lock:
                closing = self._closing
            if closing:
                client.close()
                return
            try:
                server = socket.create_connection(self._upstream)
            except OSError:
                client.close()
                continue
            link = _Link(client, server)
            with self._lock:
                self._open.update((client, server))
            for target, name in ((self._client_to_server, "up"), (self._server_to_client, "down")):
                thread = threading.Thread(target=target, args=(link,), name=f"tabrion-fault-proxy-{name}", daemon=True)
                self._threads.append(thread)
                thread.start()

    def _take(self, link: _Link, kind: bytes, body: bytes) -> str | None:
        """The fault to fire for this client message, if it carries the armed statement (disarms it)."""
        if kind not in (b"Q", b"P"):
            return None
        with self._lock:
            if self._armed is None or self._armed[0] not in body:
                return None
            _statement, fault, delay, link.then = self._armed
            self._armed = None
            self.fired += 1
            self.fired_backend = link.backend
        if fault == "delay":
            link.delay_until = time.monotonic() + delay
        return fault

    def _mute(self, link: _Link) -> None:
        """From now on no server message of ``link`` reaches the client (``inflight``, before the batch goes out)."""
        link.muted.set()

    def _run_then(self, link: _Link) -> None:
        then, link.then = link.then, None
        if then is None:
            return
        try:
            then()
        except BaseException as exc:  # reported to the test by check()
            self.error = exc

    def _client_to_server(self, link: _Link) -> None:
        buffer = b""
        started = False  # the first message (startup, SSL or cancel request) has no type byte
        fault: str | None = None
        try:
            while True:
                data = link.client.recv(65536)
                if not data:
                    break
                buffer += data
                out = b""
                while True:
                    if not started:
                        if len(buffer) < 4:
                            break
                        size = int.from_bytes(buffer[:4], "big")
                        if len(buffer) < size:
                            break
                        out, buffer, started = out + buffer[:size], buffer[size:], True
                        continue
                    if len(buffer) < 5:
                        break
                    size = 1 + int.from_bytes(buffer[1:5], "big")
                    if len(buffer) < size:
                        break
                    message, buffer = buffer[:size], buffer[size:]
                    if fault != "inflight":
                        fault = self._take(link, message[:1], message[5:]) or fault
                        if fault == "inflight":
                            # Muted before the batch goes out: otherwise the other thread could relay the server's
                            # (fast) answer before this one closes both sides, and the client would see the statement
                            # succeed (TB-143). The client side stays open, so a server message that arrives
                            # meanwhile cannot make the other thread close the link before the batch is out.
                            self._mute(link)
                    if fault == "request":
                        if out:
                            link.server.sendall(out)
                        return
                    if fault == "inflight":
                        out += message
                        if message[:1] in _BATCH_END:
                            link.server.sendall(out + buffer)  # the whole batch, and what else the client sent
                            return
                        continue
                    if fault == "reply":
                        link.dropping.set()  # before the statement can reach the server
                    out += message
                if out:
                    link.server.sendall(out)
        except OSError:
            pass
        finally:
            self._close(link)
            if fault in ("request", "inflight"):
                self._run_then(link)

    def _server_to_client(self, link: _Link) -> None:
        buffer = b""
        try:
            while True:
                data = link.server.recv(65536)
                if not data:
                    break
                buffer += data
                out = b""
                while len(buffer) >= 5:
                    size = 1 + int.from_bytes(buffer[1:5], "big")
                    if len(buffer) < size:
                        break
                    message, buffer = buffer[:size], buffer[size:]
                    if message[:1] == b"K" and size >= 9:  # BackendKeyData: the server process id
                        link.backend = int.from_bytes(message[5:9], "big")
                    if link.muted.is_set():  # an inflight cut: nothing reaches the client any more
                        with self._lock:
                            self.muted_messages += 1
                        continue
                    if not link.dropping.is_set():
                        out += message
                        continue
                    if message[:1] == b"Z":  # the server ended the statement: it ran (and committed) without us
                        if out:
                            link.client.sendall(out)
                        self._run_then(link)
                        with self._lock:
                            self.replies_lost += 1
                        return
                if out:
                    until, link.delay_until = link.delay_until, None
                    if until is not None:
                        time.sleep(max(0.0, until - time.monotonic()))
                        self._run_then(link)
                    link.client.sendall(out)
        except OSError:
            pass
        finally:
            self._close(link)

    def _close(self, link: _Link) -> None:
        for sock in (link.client, link.server):
            _cut(sock)
        with self._lock:
            self._open.difference_update((link.client, link.server))


def _cut(sock: socket.socket) -> None:
    with suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with suppress(OSError):
        sock.close()


def wait_backend_gone(conninfo: str, backend: int, *, timeout: float = 30.0) -> bool:
    """Wait until the server process ``backend`` has ended (it ran what it had received); ``False`` after timeout.

    After an ``inflight`` cut the server may still run the statement: count its effects only after this returns
    (nodestro-runtime's ``lost_statement_wait`` waits the same way).
    """
    import psycopg

    deadline = time.monotonic() + timeout
    with psycopg.connect(conninfo, autocommit=True) as connection:
        while True:
            row = connection.execute(
                "SELECT 1 FROM pg_catalog.pg_stat_activity WHERE pid OPERATOR(pg_catalog.=) %s", (backend,)
            ).fetchone()
            if row is None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
