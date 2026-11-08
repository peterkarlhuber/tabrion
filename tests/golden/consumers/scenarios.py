"""The scenarios behind the consumer golden files (TAB-022a): one per row of contract section 8 (category A/B).

Each scenario takes the implementation as an *adapter* (a few callables) and returns what a caller observes, as plain
JSON data. ``scripts/record_golden.py`` runs it with the consumer's own code (installed at a pinned commit in a
throwaway virtual environment without Tabrion) and stores the result in ``tests/golden/<consumer>.json``; the tests
(``tests/postgres/test_golden_consumers.py``, ``tests/unit/test_golden_consumers.py``) run the same scenario with
Tabrion's adapter and compare. This module imports only the standard library and psycopg, so it runs in both
environments.

On the server every scenario works in a scratch database ``<prefix>db_<hex>`` and roles ``<prefix><label>_<hex>``
that it creates and drops itself: nothing else on the server is touched (a shared server).
"""

# The SQL is built from names this module generates and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time
from typing import Any

GUCS = (
    "statement_timeout",
    "lock_timeout",
    "idle_in_transaction_session_timeout",
    "application_name",
    "TimeZone",
    "search_path",
)
"""The settings a connection setup may change; a scenario reports only the ones that changed."""

CANARY = "canary-4711@example.com"
"""A value that must never appear in an error text (B-06-1)."""


GOLDEN = Path(__file__).resolve().parent.parent
"""``tests/golden``: the consumer files ``<consumer>.json`` that ``scripts/record_golden.py`` writes."""


def golden(consumer: str) -> dict[str, Any]:
    """The records of ``tests/golden/<consumer>.json``."""
    import json

    data = json.loads((GOLDEN / f"{consumer}.json").read_text(encoding="utf-8"))
    return dict(data["records"])


def with_params(url: str, **changes: Any) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    params.update(changes)
    return str(make_conninfo("", **params))


@dataclass
class Role:
    label: str
    name: str
    url: str = field(repr=False)  # holds the password


@dataclass
class Scratch:
    """A scratch database and LOGIN roles without superuser, all with ``prefix``; dropped again on exit."""

    admin_url: str = field(repr=False)
    prefix: str
    name: str = ""
    url: str = field(default="", repr=False)
    roles: list[Role] = field(default_factory=list)

    def __enter__(self) -> Scratch:
        import psycopg
        from psycopg import sql

        self.name = f"{self.prefix}db_{secrets.token_hex(4)}"
        with psycopg.connect(self.admin_url, autocommit=True, connect_timeout=10) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
        self.url = with_params(self.admin_url, dbname=self.name)
        return self

    def role(self, label: str, attributes: str = "NOBYPASSRLS") -> Role:
        import psycopg
        from psycopg import sql

        name = f"{self.prefix}{label}_{secrets.token_hex(3)}"
        password = secrets.token_hex(16)
        with psycopg.connect(self.admin_url, autocommit=True, connect_timeout=10) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE {} PASSWORD {}").format(
                    sql.Identifier(name), sql.SQL(attributes), sql.Literal(password)
                )
            )
        role = Role(label, name, with_params(self.url, user=name, password=password))
        self.roles.append(role)
        return role

    def admin(self) -> Any:
        import psycopg

        return psycopg.connect(self.url, autocommit=True, connect_timeout=10)

    def labels(self) -> dict[str, str]:
        """Real role names to their labels (and the admin's name to ``<admin>``), for comparable results."""
        import psycopg

        with psycopg.connect(self.url, autocommit=True, connect_timeout=10) as conn:
            admin = str(conn.execute("SELECT current_user").fetchone()[0])  # type: ignore[index]
        return {admin: "<admin>", self.name: "<database>", **{r.name: f"<{r.label}>" for r in self.roles}}

    def __exit__(self, *exc: object) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.admin_url, autocommit=True, connect_timeout=10) as conn:
            if self.name:
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(self.name)))
            names = [r.name for r in self.roles]
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = ANY (%s)"
                " AND pid <> pg_backend_pid()",
                (names,),
            )
            for name in reversed(names):
                conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def normalize(value: Any, labels: Mapping[str, str]) -> Any:
    """Replace generated names by labels, recursively; tuples become lists (JSON)."""
    if isinstance(value, str):
        return labels.get(value, value)
    if isinstance(value, Mapping):
        return {str(k): normalize(v, labels) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v, labels) for v in value]
    return value


def _gucs(conn: Any) -> dict[str, str]:
    return {name: str(conn.execute(f"SHOW {name}").fetchone()[0]) for name in GUCS}


# --- PostgreSQL: connection setup (Axentra Database._configure, Nodestro PostgresDatabase._configure) ----------------


def configure_scenario(scratch: Scratch, configure: Callable[[Any], None], *, autocommit: bool) -> dict[str, Any]:
    """What ``configure(conn)`` changes on a fresh connection, and the transaction state it leaves."""
    import psycopg

    with psycopg.connect(scratch.url, autocommit=autocommit, connect_timeout=10) as conn:
        before = _gucs(conn)
        if not autocommit:
            conn.rollback()
        configure(conn)
        status = conn.info.transaction_status.name
        after = _gucs(conn)
        if not autocommit:
            conn.rollback()
    return {"changed": {k: after[k] for k in GUCS if after[k] != before[k]}, "status_after": status}


# --- PostgreSQL: ping ------------------------------------------------------------------------------------------------


def ping_scenario(scratch: Scratch, ping: Callable[[Any], bool]) -> dict[str, Any]:
    """``ping(pool)`` on a working pool and on a pool whose server does not answer."""
    from psycopg_pool import ConnectionPool

    result: dict[str, Any] = {}
    healthy = ConnectionPool(scratch.url, min_size=1, max_size=1, open=False)
    healthy.open(wait=True, timeout=10)
    try:
        result["healthy"] = bool(ping(healthy))
    finally:
        healthy.close()
    dead = ConnectionPool(
        with_params(scratch.url, host="127.0.0.1", port="1", connect_timeout="1"), min_size=1, max_size=1, open=False
    )
    dead.open(wait=False)
    try:
        result["unreachable"] = bool(ping(dead))
    finally:
        dead.close(timeout=1)
    return result


# --- PostgreSQL: role_info / check_role ------------------------------------------------------------------------------


ROLE_CASES = ("superuser", "plain", "bypassrls", "rls_table_owner", "plain_table_owner", "member_of_rls_owner")
CHECKS = ((False, False), (False, True), (True, False), (True, True))


def role_scenario(
    scratch: Scratch,
    info: Callable[[str], Mapping[str, Any]],
    check: Callable[[str, bool, bool], bool] | None,
) -> dict[str, Any]:
    """``info(url)`` and ``check(url, allow_superuser, allow_table_owner)`` (``True``: accepted; left out when
    ``check`` is ``None``) for each role case.

    The tables are in ``public``, the first schema of every role's ``search_path``: ``rls_table`` with row-level
    security owned by ``rls_table_owner``, ``plain_table`` without it owned by ``plain_table_owner``."""
    urls: dict[str, str] = {"superuser": scratch.url}
    for label, attributes in (
        ("plain", "NOBYPASSRLS"),
        ("bypassrls", "BYPASSRLS"),
        ("rls_table_owner", "NOBYPASSRLS"),
        ("plain_table_owner", "NOBYPASSRLS"),
        ("member_of_rls_owner", "NOBYPASSRLS"),
    ):
        urls[label] = scratch.role(label, attributes).url
    names = {r.label: r.name for r in scratch.roles}
    with scratch.admin() as conn:
        conn.execute("CREATE TABLE public.rls_table (id int)")
        conn.execute("ALTER TABLE public.rls_table ENABLE ROW LEVEL SECURITY")
        conn.execute(f'ALTER TABLE public.rls_table OWNER TO "{names["rls_table_owner"]}"')
        conn.execute("CREATE TABLE public.plain_table (id int)")
        conn.execute(f'ALTER TABLE public.plain_table OWNER TO "{names["plain_table_owner"]}"')
        conn.execute(f'GRANT "{names["rls_table_owner"]}" TO "{names["member_of_rls_owner"]}"')
    labels = scratch.labels()
    result: dict[str, Any] = {}
    for case in ROLE_CASES:
        result[case] = {"info": normalize(dict(info(urls[case])), labels)}
        if check is not None:
            result[case]["accepted"] = {
                f"superuser={s},table_owner={o}": bool(check(urls[case], s, o)) for s, o in CHECKS
            }
    return result


# --- PostgreSQL: inspect_role ----------------------------------------------------------------------------------------


INSPECT_CASES = ("runtime", "owns_one", "bypassrls", "no_connect", "can_create", "owner")


def inspect_scenario(scratch: Scratch, inspect: Callable[[str, str, str], Mapping[str, Any]]) -> dict[str, Any]:
    """``inspect(owner_url, role, schema)`` run as the owner (no superuser) of schema ``app`` with three tables."""
    owner = scratch.role("owner")
    roles = {
        label: scratch.role(label, "BYPASSRLS" if label == "bypassrls" else "NOBYPASSRLS")
        for label in ("runtime", "owns_one", "bypassrls", "no_connect", "can_create")
    }
    with scratch.admin() as conn:
        conn.execute(f'REVOKE CONNECT ON DATABASE "{scratch.name}" FROM PUBLIC')
        for label in ("owner", "runtime", "owns_one", "bypassrls", "can_create"):
            role = owner if label == "owner" else roles[label]
            conn.execute(f'GRANT CONNECT ON DATABASE "{scratch.name}" TO "{role.name}"')
        conn.execute(f'CREATE SCHEMA app AUTHORIZATION "{owner.name}"')
        conn.execute(f'GRANT CREATE ON SCHEMA app TO "{roles["can_create"].name}"')
    import psycopg

    with psycopg.connect(owner.url, autocommit=True, connect_timeout=10) as conn:
        conn.execute("CREATE TABLE app.alpha (id int)")
        conn.execute("CREATE TABLE app.beta (id int) PARTITION BY RANGE (id)")
        conn.execute("CREATE VIEW app.gamma AS SELECT 1 AS one")
    with scratch.admin() as conn:
        conn.execute("CREATE TABLE app.delta (id int)")
        conn.execute(f'ALTER TABLE app.delta OWNER TO "{roles["owns_one"].name}"')
    labels = scratch.labels()
    result: dict[str, Any] = {}
    for case in INSPECT_CASES:
        name = owner.name if case == "owner" else roles[case].name
        result[case] = normalize(dict(inspect(owner.url, name, "app")), labels)
    return result


# --- PostgreSQL: advisory locks --------------------------------------------------------------------------------------


def _holds(conn: Any, key: int) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = %s"
        " AND ((classid::bigint << 32) | objid::bigint) = (%s::bigint & x'FFFFFFFFFFFFFFFF'::bigint)",
        (conn.info.backend_pid, key),
    ).fetchone()
    return bool(row[0])


def wait_lock_scenario(scratch: Scratch, wait: Callable[[Any, int], Any], key: int) -> dict[str, Any]:
    """``wait(conn, key)`` on a free lock, and on one another session releases after 0.7 s."""
    import psycopg

    result: dict[str, Any] = {}
    with psycopg.connect(scratch.url, autocommit=True, connect_timeout=10) as conn:
        wait(conn, key)
        result["free"] = {"held_after": _holds(conn, key)}
        conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
    with (
        psycopg.connect(scratch.url, autocommit=True, connect_timeout=10) as other,
        psycopg.connect(scratch.url, autocommit=True, connect_timeout=10) as conn,
    ):
        other.execute("SELECT pg_advisory_lock(%s)", (key,))
        timer = threading.Timer(0.7, lambda: other.execute("SELECT pg_advisory_unlock(%s)", (key,)))
        start = time.monotonic()
        timer.start()
        try:
            wait(conn, key)
            elapsed = time.monotonic() - start
        finally:
            timer.join()
        result["contended"] = {"returned_after_release": elapsed >= 0.6, "held_after": _holds(conn, key)}
        conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
    return result


def _end_lock_holders(scratch: Scratch, key: int) -> int:
    with scratch.admin() as conn:
        rows = conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' AND granted"
            " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            " AND ((classid::bigint << 32) | objid::bigint) = (%s::bigint & x'FFFFFFFFFFFFFFFF'::bigint)",
            (key,),
        ).fetchall()
    time.sleep(0.3)
    return len(rows)


def leader_lock_scenario(scratch: Scratch, make: Callable[[str, int], Any], key: int) -> list[Any]:
    """A leader election: ``make(url, key)`` gives an object with ``acquire() -> bool`` and ``release()``."""
    a, b = make(scratch.url, key), make(scratch.url, key)
    steps: list[Any] = [
        ["a.acquire", a.acquire()],
        ["b.acquire", b.acquire()],
        ["a.acquire again", a.acquire()],
    ]
    steps.append(["a's connection ended", _end_lock_holders(scratch, key)])
    steps.append(["a.acquire after the loss", a.acquire()])
    steps.append(["b.acquire", b.acquire()])
    a.release()
    steps.append(["b.acquire after a.release", b.acquire()])
    b.release()
    steps.append(["a.acquire after b.release", a.acquire()])
    a.release()
    return steps


def session_lock_scenario(scratch: Scratch, make: Callable[[str], Any], key: int) -> list[Any]:
    """Counted session locks: ``make(url)`` gives an object with ``try_(key)``, ``holds(key)``, ``release(key)``."""
    a, b = make(scratch.url), make(scratch.url)
    steps: list[Any] = []

    def step(label: str, value: Any) -> None:
        steps.append([label, value])

    step("a.try", a.try_(key))
    step("b.try", b.try_(key))
    step("a.try again (count 2)", a.try_(key))
    a.release(key)
    step("b.try after one release", b.try_(key))
    step("a.holds", a.holds(key))
    a.release(key)
    step("a.holds after the second release", a.holds(key))
    step("b.try after the last release", b.try_(key))
    b.release(key)
    step("a.try", a.try_(key))
    step("a's connection ended", _end_lock_holders(scratch, key))
    step("a.holds after the loss", a.holds(key))
    step("a.try after the loss", a.try_(key))
    step("b.try", b.try_(key))
    a.release(key)
    for lock in (a, b):
        with contextlib.suppress(Exception):
            lock.close()
    return steps


# --- PostgreSQL: error texts (B-06-1) ---------------------------------------------------------------------------------


def _errors(scratch: Scratch) -> Iterator[tuple[str, BaseException]]:
    """Real server errors, several of them carrying :data:`CANARY` in their ``DETAIL``."""
    import psycopg

    plain = scratch.role("plain")
    with scratch.admin() as conn:
        conn.execute(
            "CREATE TABLE parent (id int PRIMARY KEY, email text UNIQUE);"
            " CREATE TABLE child (id int, parent int REFERENCES parent, CONSTRAINT positive CHECK (id > 0),"
            "   note varchar(4), amount smallint, needed text NOT NULL DEFAULT 'x');"
            f" INSERT INTO parent VALUES (1, '{CANARY}')"
        )
        statements = {
            "unique_violation": f"INSERT INTO parent VALUES (2, '{CANARY}')",
            "foreign_key_violation": "INSERT INTO child (id, parent) VALUES (1, 4711)",
            "check_violation": "INSERT INTO child (id) VALUES (-4711)",
            "not_null_violation": "INSERT INTO child (id, needed) VALUES (1, NULL)",
            "string_data_right_truncation": f"INSERT INTO child (id, note) VALUES (1, '{CANARY}')",
            "numeric_value_out_of_range": "INSERT INTO child (id, amount) VALUES (1, 4711471)",
            "invalid_text_representation": f"SELECT '{CANARY}'::int",
            "division_by_zero": "SELECT 4711 / 0",
            "undefined_table": "SELECT * FROM no_such_table",
            "undefined_column": "SELECT no_such_column FROM parent",
            "syntax_error": "SELEC 1",
            "query_canceled": "SET statement_timeout = 1; SELECT pg_sleep(1)",
        }
        for label, statement in statements.items():
            try:
                conn.execute(statement)
            except psycopg.Error as exc:
                yield label, exc
            conn.execute("RESET statement_timeout")
    with psycopg.connect(plain.url, autocommit=True, connect_timeout=10) as conn:
        try:
            conn.execute("SELECT * FROM parent")
        except psycopg.Error as exc:
            yield "insufficient_privilege", exc
    with scratch.admin() as holder, scratch.admin() as conn:
        holder.execute("BEGIN")
        holder.execute("LOCK TABLE parent")
        try:
            conn.execute("SET lock_timeout = 50; SELECT * FROM parent")
        except psycopg.Error as exc:
            yield "lock_not_available", exc
        holder.execute("ROLLBACK")


def error_text_scenario(scratch: Scratch, message: Callable[[BaseException], str]) -> dict[str, Any]:
    """``message(error)`` for each server error; also whether the canary value leaked into it."""
    result: dict[str, Any] = {}
    for label, exc in _errors(scratch):
        text = message(exc)
        result[label] = {"sqlstate": getattr(exc, "sqlstate", None), "message": text, "leaks": CANARY in text}
    return result


# --- PostgreSQL: pgvector status (Nodestro pgvector_status) ----------------------------------------------------------


def vector_scenario(scratch: Scratch, status: Callable[[str], Mapping[str, Any]]) -> dict[str, Any]:
    """``status(url)`` on a connection whose ``search_path`` is ``app``: not installed, installed in ``public`` (not
    visible), installed in ``app``. Without pgvector on the server only ``unavailable`` is reported."""
    with scratch.admin() as conn:
        conn.execute("CREATE SCHEMA app")
        available = conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'").fetchone()
    url = with_params(scratch.url, options="-c search_path=app")
    keys = ("state", "command", "extension_schema")
    result: dict[str, Any] = {"server_has_vector": available is not None}
    result["not_installed"] = {k: status(url).get(k) for k in keys}
    if available is None:
        return result
    with scratch.admin() as conn:
        conn.execute("CREATE EXTENSION vector SCHEMA public")
    result["in_public"] = {k: status(url).get(k) for k in keys}
    with scratch.admin() as conn:
        conn.execute("ALTER EXTENSION vector SET SCHEMA app")
    result["in_app"] = {k: status(url).get(k) for k in keys}
    return result


# --- SQLite ----------------------------------------------------------------------------------------------------------

PRAGMAS = ("journal_mode", "foreign_keys", "busy_timeout", "synchronous", "trusted_schema", "secure_delete")


def sqlite_open_scenario(directory: Path, open_: Callable[[Path], sqlite3.Connection]) -> dict[str, Any]:
    """The pragmas and the file mode of a new database opened by ``open_(path)`` (umask 022)."""
    path = directory / "open.sqlite3"
    old = os.umask(0o022)
    try:
        conn = open_(path)
    finally:
        os.umask(old)
    try:
        values = {name: conn.execute(f"PRAGMA {name}").fetchone()[0] for name in PRAGMAS}
        values["isolation_level"] = conn.isolation_level
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
    finally:
        conn.close()
    values["file_mode"] = oct(stat.S_IMODE(path.stat().st_mode)) if os.name == "posix" else None
    return values


def sqlite_transaction_scenario(
    directory: Path, open_: Callable[[Path], sqlite3.Connection], transaction: Callable[[sqlite3.Connection], Any]
) -> dict[str, Any]:
    """``with transaction(conn):`` on a database ``open_(path)`` opened: the lock it takes and what it keeps."""
    path = directory / "tx.sqlite3"
    conn = open_(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        other = sqlite3.connect(path, timeout=0.05, isolation_level=None)
        result: dict[str, Any] = {}
        with transaction(conn):
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute("ROLLBACK")
                result["other_writer_blocked_before_any_write"] = False
            except sqlite3.OperationalError:
                result["other_writer_blocked_before_any_write"] = True
            conn.execute("INSERT INTO t VALUES (1)")
        result["committed"] = other.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        with contextlib.suppress(RuntimeError), transaction(conn):
            conn.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("roll back")
        result["rolled_back_on_error"] = other.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        result["in_transaction_after"] = conn.in_transaction
        other.close()
    finally:
        conn.close()
    return result


def sqlite_migrate_scenario(
    directory: Path,
    open_: Callable[[Path], sqlite3.Connection],
    migrate: Callable[[sqlite3.Connection, Mapping[int, Callable[[sqlite3.Connection], None]], int, Any], Any],
) -> dict[str, Any]:
    """``migrate(conn, migrations, target, backup)``: the outcome (a code suffix, or the version it returned), the
    ``user_version`` afterwards and the calls of ``backup(current, target)`` for each case."""

    def step(sql: str) -> Callable[[sqlite3.Connection], None]:
        def run(c: sqlite3.Connection) -> None:
            c.execute(sql)

        return run

    def failing(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE half (x)")
        raise RuntimeError("step fails")

    good = {1: step("CREATE TABLE one (x)"), 2: step("CREATE TABLE two (x)")}
    cases: dict[str, Any] = {
        "fresh_no_backup": (False, good, 2, None),
        "data_no_backup": (True, good, 2, None),
        "data_with_backup": (True, good, 2, "ok"),
        "backup_fails": (True, good, 2, "fail"),
        "step_fails": (True, {1: step("CREATE TABLE one (x)"), 2: failing}, 2, "ok"),
        "missing_step": (True, {1: good[1]}, 2, "ok"),
        "same_version": (True, good, 0, "ok"),
        "too_new": (True, good, 0, "ok", 3),
    }
    result: dict[str, Any] = {}
    for label, spec in cases.items():
        data, migrations, target, backup_kind = spec[:4]
        path = directory / f"migrate-{label}.sqlite3"
        conn = open_(path)
        try:
            if len(spec) > 4:
                conn.execute(f"PRAGMA user_version = {spec[4]}")
            if data:
                conn.execute("CREATE TABLE IF NOT EXISTS existing (x)")
                conn.execute("INSERT INTO existing VALUES (1)")
            calls: list[list[int]] = []

            def backup(current: int, wanted: int, kind: Any = backup_kind, calls: list[list[int]] = calls) -> None:
                calls.append([current, wanted])
                if kind == "fail":
                    raise OSError("disk full")

            try:
                outcome: Any = {"returned": migrate(conn, migrations, target, backup if backup_kind else None)}
            except Exception as exc:  # the consumer's or Tabrion's error with a code
                code = str(getattr(exc, "code", type(exc).__name__))
                outcome = {"error": code.rsplit(".", 1)[-1]}
            outcome["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
            outcome["half_table"] = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone())
            outcome["backup_calls"] = calls
            result[label] = outcome
        finally:
            conn.close()
    return result


def sqlite_backup_scenario(
    directory: Path, open_: Callable[[Path], sqlite3.Connection], backup: Callable[[sqlite3.Connection, Path], Any]
) -> dict[str, Any]:
    """``backup(conn, target)`` into a new file and over an existing one: rows copied, the copy's mode."""
    conn = open_(directory / "source.sqlite3")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.execute("INSERT INTO t VALUES (1), (2)")
        result: dict[str, Any] = {}
        for label in ("new_target", "existing_target"):
            target = directory / "copy.sqlite3"
            if label == "existing_target":
                with sqlite3.connect(target) as old:
                    old.execute("CREATE TABLE IF NOT EXISTS stale (y)")
                old.close()
            backup(conn, target)
            with contextlib.closing(sqlite3.connect(target)) as copy:
                rows = copy.execute("SELECT count(*) FROM t").fetchone()[0]
                stale = bool(copy.execute("SELECT 1 FROM sqlite_master WHERE name = 'stale'").fetchone())
            result[label] = {"rows": rows, "stale_table_kept": stale}
    finally:
        conn.close()
    return result


def sqlite_check_scenario(directory: Path, check: Callable[[Path], Mapping[str, Any]]) -> dict[str, Any]:
    """``check(path)`` on a healthy WAL database, a damaged one and a file that is no database."""
    healthy = directory / "healthy.sqlite3"
    with contextlib.closing(sqlite3.connect(healthy)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x)")
        conn.executemany("INSERT INTO t VALUES (?)", [(str(i) * 100,) for i in range(200)])
        conn.commit()
    damaged = directory / "damaged.sqlite3"
    data = bytearray(healthy.read_bytes())
    for offset in range(4096 + 100, min(len(data), 3 * 4096), 7):  # inside the table's pages, past the header
        data[offset] = 0xFF
    damaged.write_bytes(bytes(data))
    garbage = directory / "garbage.sqlite3"
    garbage.write_bytes(b"this is not a SQLite database" * 200)
    return {
        label: dict(check(path))
        for label, path in (("healthy", healthy), ("damaged", damaged), ("not_a_database", garbage))
    }


def sqlite_symlink_scenario(directory: Path, open_: Callable[[Path], sqlite3.Connection]) -> dict[str, Any]:
    """``open_(path)`` on a symbolic link to a database and on a database whose WAL file is a link: refused or
    opened (and whether the link's target was left unchanged)."""
    result: dict[str, Any] = {}
    if os.name != "posix":
        return result
    real = directory / "real.sqlite3"
    with contextlib.closing(sqlite3.connect(real)) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
    before = real.read_bytes()
    link = directory / "link.sqlite3"
    link.symlink_to(real)
    side = directory / "side.sqlite3"
    (directory / "elsewhere").write_bytes(b"")
    Path(str(side) + "-wal").symlink_to(directory / "elsewhere")
    for label, path in (("database_is_link", link), ("wal_is_link", side)):
        try:
            open_(path).close()
            result[label] = "opened"
        except Exception:
            result[label] = "refused"
    result["target_unchanged"] = real.read_bytes() == before
    return result


def sqlite_read_scenario(
    directory: Path, open_: Callable[[Path], sqlite3.Connection], read: Callable[[sqlite3.Connection], Any]
) -> dict[str, Any]:
    """``with read(conn):`` on a WAL database: one snapshot for every read inside it, nothing left open after it."""
    path = directory / "read.sqlite3"
    conn = open_(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        if conn.in_transaction:
            conn.commit()
        conn.execute("INSERT INTO t VALUES (1)")
        if conn.in_transaction:
            conn.commit()
        result: dict[str, Any] = {"journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0]}
        with contextlib.closing(sqlite3.connect(path, timeout=0.5, isolation_level=None)) as other:
            with read(conn):
                first = conn.execute("SELECT count(*) FROM t").fetchone()[0]
                other.execute("INSERT INTO t VALUES (2)")
                second = conn.execute("SELECT count(*) FROM t").fetchone()[0]
            result["one_snapshot"] = first == second == 1
            result["in_transaction_after"] = conn.in_transaction
            result["sees_new_rows_after"] = conn.execute("SELECT count(*) FROM t").fetchone()[0] == 2
    finally:
        conn.close()
    return result


# --- PostgreSQL: a connection source (Orkexa PsycopgSource) ----------------------------------------------------------


def source_scenario(scratch: Scratch, make: Callable[[str], Any]) -> dict[str, Any]:
    """What a statement sees on the connections of ``make(url)`` (an object with ``transaction(mode)`` and
    ``close()``): the settings it changed, the transaction state per mode, and whether a broken statement inside
    ``"transaction"`` rolls the earlier ones back."""
    import psycopg

    with psycopg.connect(scratch.url, autocommit=True, connect_timeout=10) as plain:
        before = _gucs(plain)
        plain.execute("CREATE TABLE source_rows (x int)")
    source = make(scratch.url)
    result: dict[str, Any] = {}
    try:
        for mode in ("read", "write", "transaction"):
            with source.transaction(mode) as conn:
                after = _gucs(conn)
                result[mode] = {
                    "changed": {k: after[k] for k in GUCS if after[k] != before[k]},
                    "status": conn.info.transaction_status.name,
                }
        with contextlib.suppress(Exception), source.transaction("transaction") as conn:
            conn.execute("INSERT INTO source_rows VALUES (1)")
            conn.execute("INSERT INTO source_rows VALUES ('x')")
        with source.transaction("read") as conn:
            result["rolled_back_on_error"] = conn.execute("SELECT count(*) FROM source_rows").fetchone()[0] == 0
    finally:
        source.close()
    return result


# --- Environment variables -------------------------------------------------------------------------------------------


def url_file_scenario(directory: Path, read: Callable[[Mapping[str, str]], Any], name: str) -> dict[str, Any]:
    """``read(environ)`` for ``name`` given directly, through ``<name>_FILE``, both, an unreadable or empty file, or
    neither: the value, or ``"error"``."""
    secret = directory / "secret.txt"
    secret.write_text("  postgresql://u:pw@h/db\n", encoding="utf-8")
    empty = directory / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    cases = {
        "direct": {name: " postgresql://u:pw@h/db "},
        "file": {f"{name}_FILE": str(secret)},
        "both": {name: "postgresql://u:pw@h/db", f"{name}_FILE": str(secret)},
        "unreadable_file": {f"{name}_FILE": str(directory / "missing.txt")},
        "empty_file": {f"{name}_FILE": str(empty)},
        "neither": {},
    }
    result: dict[str, Any] = {}
    for label, environ in cases.items():
        try:
            result[label] = read(environ)
        except Exception:
            result[label] = "error"
    return result


def environment_names(source: str) -> dict[str, list[str]]:
    """The environment variables a test support module reads, by role, from its source (``os.environ.get("X")``
    and ``os.environ["X"]``), and which of them it compares with ``== "1"``."""
    import ast

    tree = ast.parse(source)
    names: set[str] = set()
    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.isupper():
                names.add(first.value)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            value = node.slice.value
            if isinstance(value, str) and value.isupper() and "_" in value:
                names.add(value)
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and right.value == "1" and isinstance(node.left, ast.Call):
                arg = node.left.args[0] if node.left.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    flags.add(arg.value)
    return {"read": sorted(names), "flag_equal_1": sorted(flags)}
