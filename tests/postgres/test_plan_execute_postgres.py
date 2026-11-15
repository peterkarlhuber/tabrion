"""The executor of schema change plans on PostgreSQL (TABRION TAB-XW-5, contract I-125; ADR 0015 section 4, N-1,
D-5.4; the review of #89, P89-03).

``execute_plan`` locks the tables its steps change, then fingerprints and plans again under the locks, inside its
own ``READ COMMITTED`` transaction; drift (a column, a trigger function body, a policy, a disabled trigger) aborts
before a statement runs; a lock that is not granted within ``lock_timeout`` raises ``TransientError`` with the step;
a failing step rolls back; ``CONCURRENTLY`` steps run alone, in plan order, and a failure there reports ``partial``.
Concurrent sessions in these tests are plain psycopg connections the test opens.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import dataclasses
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

sqlalchemy = pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database  # noqa: E402
from tabrion import plan as tp  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import PlanFailed, PlanMismatch, PlanRefused, SchemaDrift, TransientError  # noqa: E402
from tabrion.testing import PostgresSchema  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

SECRET = "SECRET-4711"


@pytest.fixture
def db(tabrion_postgres_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(tabrion_postgres_url))
    yield database
    database.close()


def take(db: Database, s: str) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, level=2, schemas=[s])


def ddl(url: str, *statements: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def rows(url: str, s: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as conn:
        return list(conn.execute(f"SELECT id, code FROM {s}.parts ORDER BY id").fetchall())


TOUCH = (
    "CREATE OR REPLACE FUNCTION {s}.touch() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN NEW.code := upper(NEW.code); RETURN NEW; END $$"
)


@pytest.fixture
def shop(tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    s = tabrion_postgres_schema.name
    ddl(
        tabrion_postgres_url,
        f"CREATE TABLE {s}.parts (id bigint PRIMARY KEY, code varchar(20), qty integer)",
        f"CREATE INDEX parts_code ON {s}.parts (code)",
        f"CREATE TABLE {s}.moves (id bigint PRIMARY KEY, part bigint REFERENCES {s}.parts (id))",
        TOUCH.format(s=s),
        f"CREATE TRIGGER parts_touch BEFORE INSERT ON {s}.parts FOR EACH ROW EXECUTE FUNCTION {s}.touch()",
        f"ALTER TABLE {s}.parts ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY parts_all ON {s}.parts USING (qty IS NULL OR qty >= 0)",
        f"INSERT INTO {s}.parts VALUES (1, '{SECRET}', NULL), (2, 'b%:x', 3)",
        f"INSERT INTO {s}.moves VALUES (1, 1)",
    )
    return s


def table(model: ts.SchemaSnapshot, name: str) -> ts.TableShape:
    found = model.table(name)
    assert found is not None
    return found


def replace_table(model: ts.SchemaSnapshot, name: str, **changes: Any) -> ts.SchemaSnapshot:
    tables = tuple(dataclasses.replace(t, **changes) if t.name == name else t for t in model.tables)
    return dataclasses.replace(model, tables=tables)


def with_note(model: ts.SchemaSnapshot, s: str, default: str | None = "'50%: n/a'") -> ts.SchemaSnapshot:
    """A column on ``parts`` with a default holding ``%`` and ``:`` (no bind parameter, no ``%`` formatting)."""
    parts = table(model, f"{s}.parts")
    note = ts.ColumnShape("note", "TEXT", True, default=default)
    return replace_table(model, f"{s}.parts", columns=(*parts.columns, note))


def with_changed_index(model: ts.SchemaSnapshot, s: str) -> ts.SchemaSnapshot:
    """``parts_code`` on ``(code, id)``: dropped and created again."""
    parts = table(model, f"{s}.parts")
    indexes = tuple(dataclasses.replace(i, columns=("code", "id")) for i in parts.indexes)
    return replace_table(model, f"{s}.parts", indexes=indexes)


def run(
    db: Database, s: str, current: ts.SchemaSnapshot, target: ts.SchemaSnapshot, **options: Any
) -> tp.ExecutionResult:
    concurrent = bool(options.get("concurrent_indexes", False))
    preview = tp.plan(current, target, concurrent_indexes=concurrent)
    arguments: dict[str, Any] = {
        "plan_hash": preview.plan_hash,
        "expected_fingerprint": ts.fingerprint(current),
        "environment": "test",
        "allow": preview.risks,
        "schemas": [s],
        "source": current,
    }
    arguments.update(options)
    return tp.execute_plan(db, target, **arguments)


def spy(monkeypatch: pytest.MonkeyPatch, on_snapshot: Callable[[int, Any], None]) -> list[int]:
    """Call ``on_snapshot(n, transaction)`` before the executor's ``n``-th snapshot (from 1) of a run."""
    calls: list[int] = []
    original = tp._Executor.snapshot

    def snapshot(self: Any, transaction: Any) -> ts.SchemaSnapshot:
        calls.append(len(calls) + 1)
        on_snapshot(len(calls), transaction)
        return original(self, transaction)

    monkeypatch.setattr(tp._Executor, "snapshot", snapshot)
    return calls


# --- runs ------------------------------------------------------------------------------------------------------------


def test_run_reaches_the_target_and_keeps_rows_trigger_and_policy(
    db: Database, shop: str, tabrion_postgres_url: str
) -> None:
    s = shop
    current = take(db, s)
    target = with_note(current, s)
    before = rows(tabrion_postgres_url, s)
    result = run(db, s, current, target)
    assert result.steps_run == 1
    assert result.dry_run is False
    after = take(db, s)
    assert result.fingerprint_after == ts.fingerprint(after)
    assert [d.kind for d in ts.compare(target, after)] in ([], ["column_default"])  # the server writes '...'::text
    assert rows(tabrion_postgres_url, s) == before
    with psycopg.connect(tabrion_postgres_url) as conn:
        assert conn.execute(f"SELECT DISTINCT note FROM {s}.parts").fetchall() == [("50%: n/a",)]
        conn.execute(f"INSERT INTO {s}.parts (id, code) VALUES (3, 'low')")
        assert conn.execute(f"SELECT code FROM {s}.parts WHERE id = 3").fetchone() == ("LOW",)
    assert [t.name for t in table(after, f"{s}.parts").triggers] == ["parts_touch"]
    assert [p.name for p in table(after, f"{s}.parts").policies] == ["parts_all"]


def test_dry_run_leaves_the_fingerprint_unchanged(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    s = shop
    current = take(db, s)
    before = rows(tabrion_postgres_url, s)
    result = run(db, s, current, with_note(current, s), dry_run=True)
    assert result.dry_run is True
    assert result.steps_run == 1
    assert result.fingerprint_after is None
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)
    assert rows(tabrion_postgres_url, s) == before


def test_failing_step_rolls_back_data_unchanged(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    """A column added before a ``NOT NULL`` that fails on a row with ``NULL``: neither stays."""
    s = shop
    current = take(db, s)
    parts = table(current, f"{s}.parts")
    columns = (
        *(dataclasses.replace(c, nullable=False) if c.name == "qty" else c for c in parts.columns),
        ts.ColumnShape("extra", "INTEGER", True),
    )
    target = replace_table(current, f"{s}.parts", columns=columns)
    before = rows(tabrion_postgres_url, s)
    with pytest.raises(PlanFailed) as caught:
        run(db, s, current, target)
    error = caught.value
    assert error.details["state"] == "rolled_back"
    assert error.details["completed"] == 0
    step = tp.plan(current, target).steps[error.details["step"]]  # type: ignore[index]
    assert step.operation == "alter_column"
    assert error.__cause__ is None
    assert SECRET not in str(error)
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)
    assert rows(tabrion_postgres_url, s) == before


# --- drift ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ("ALTER TABLE {s}.moves ADD COLUMN extra integer", "column_added"),
        (
            "CREATE OR REPLACE FUNCTION {s}.touch() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN NEW.code := lower(NEW.code); RETURN NEW; END $$",
            "function_missing",  # a changed function is reported as missing and added
        ),
        ("ALTER TABLE {s}.parts DISABLE TRIGGER parts_touch", None),
        ("ALTER POLICY parts_all ON {s}.parts USING (true)", None),
        ("ALTER TABLE {s}.parts FORCE ROW LEVEL SECURITY", None),
    ],
)
def test_drift_between_preview_and_execution_refused(
    db: Database, shop: str, tabrion_postgres_url: str, change: str, kind: str | None
) -> None:
    s = shop
    current = take(db, s)
    target = with_note(current, s)
    preview = tp.plan(current, target)
    ddl(tabrion_postgres_url, change.format(s=s))
    drifted = take(db, s)
    before = rows(tabrion_postgres_url, s)
    with pytest.raises(SchemaDrift) as caught:
        run(db, s, current, target, plan_hash=preview.plan_hash)
    error = caught.value
    assert error.details["expected"] == ts.fingerprint(current)
    assert error.details["actual"] == ts.fingerprint(drifted)
    assert error.differences == ts.compare(current, drifted)
    assert error.differences
    if kind is not None:
        assert kind in {d.kind for d in error.differences}
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(drifted)
    assert rows(tabrion_postgres_url, s) == before


def test_drift_between_first_check_and_lock_refused(
    db: Database, shop: str, tabrion_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-5.4: the trigger function changes after the executor's first fingerprint but before its locks are granted;
    the fingerprint repeated under the locks sees it, and nothing runs."""
    s = shop
    current = take(db, s)

    calls = spy(monkeypatch, lambda _n, _transaction: None)
    original_lock = tp._Executor.lock

    def lock(self: Any, transaction: Any, steps: Any, snapshot: Any) -> None:
        ddl(  # another session commits a change between the first check and the locks
            tabrion_postgres_url,
            f"CREATE OR REPLACE FUNCTION {s}.touch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        )
        original_lock(self, transaction, steps, snapshot)

    monkeypatch.setattr(tp._Executor, "lock", lock)
    with pytest.raises(SchemaDrift) as caught:
        run(db, s, current, with_note(current, s))
    assert calls == [1, 2]  # the drift was found by the snapshot under the locks
    assert {"function_missing", "function_added"} <= {d.kind for d in caught.value.differences}
    assert "note" not in {c.name for c in table(take(db, s), f"{s}.parts").columns}


def test_locks_taken_before_drift_fingerprint(
    db: Database, shop: str, tabrion_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1, D-5.4: the snapshot whose fingerprint binds the run is taken while the executor holds ``ACCESS
    EXCLUSIVE`` on the table it changes and ``SHARE ROW EXCLUSIVE`` on the table whose foreign key refers to it."""
    s = shop
    current = take(db, s)
    held: dict[int, set[tuple[str, str]]] = {}

    def record(n: int, transaction: Any) -> None:
        pid = transaction.fetch_scalar("SELECT pg_backend_pid()")
        with psycopg.connect(tabrion_postgres_url) as conn:
            found = conn.execute(
                "SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE l.pid = %s AND l.granted AND n.nspname = %s AND c.relkind = 'r'",
                (pid, s),
            ).fetchall()
        held[n] = {(str(name), str(mode)) for name, mode in found}

    spy(monkeypatch, record)
    run(db, s, current, with_note(current, s))
    assert held[1] == set()  # the first check runs before any lock
    assert held[2] == {("parts", "AccessExclusiveLock"), ("moves", "ShareRowExclusiveLock")}
    assert held[3] == held[2]  # fingerprint_after, still under the locks, before the commit


def test_concurrent_ddl_after_check_blocked(
    db: Database, shop: str, tabrion_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1: once the fingerprint under the locks is taken, another session's DDL on the table waits for the commit
    (here: it gives up after its own ``lock_timeout``), so it cannot land between the check and the first step."""
    s = shop
    current = take(db, s)
    outcome: list[str] = []

    def try_ddl(n: int, _transaction: Any) -> None:
        if n != 2:
            return
        with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
            conn.execute("SET lock_timeout = '200ms'")
            try:
                conn.execute(f"ALTER TABLE {s}.parts ADD COLUMN sneaked integer")
            except psycopg.errors.LockNotAvailable:
                outcome.append("blocked")
            else:
                outcome.append("landed")

    spy(monkeypatch, try_ddl)
    target = with_note(current, s)
    run(db, s, current, target)
    assert outcome == ["blocked"]
    assert [c.name for c in table(take(db, s), f"{s}.parts").columns] == ["id", "code", "qty", "note"]


# --- lock timeout -------------------------------------------------------------------------------------------------


def test_lock_timeout_raises_transient_error_with_step(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    s = shop
    current = take(db, s)
    before = rows(tabrion_postgres_url, s)
    with psycopg.connect(tabrion_postgres_url) as holder:
        holder.execute(f"SELECT count(*) FROM {s}.parts")  # ACCESS SHARE, held until the end of its transaction
        with pytest.raises(TransientError) as caught:
            run(db, s, current, with_note(current, s), lock_timeout=0.2)
        holder.rollback()
    error = caught.value
    assert error.details["step"] == 0
    assert error.classification is not None
    assert error.classification.sqlstate == "55P03"
    assert error.__cause__ is None
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)
    assert rows(tabrion_postgres_url, s) == before


# --- refusals ----------------------------------------------------------------------------------------------------


def test_hash_mismatch_refused(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    """The user confirmed the plan without the default; the target held for execution has one."""
    s = shop
    current = take(db, s)
    confirmed = tp.plan(current, with_note(current, s, default=None))
    with pytest.raises(PlanMismatch) as caught:
        run(db, s, current, with_note(current, s), plan_hash=confirmed.plan_hash)
    assert caught.value.code == "plan.hash_mismatch"
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)


@pytest.mark.parametrize("name", ["alembic_version", "Nodestro_Schema_Migrations", "axentra_schema_migrations"])
def test_migration_managed_database_refused(db: Database, shop: str, tabrion_postgres_url: str, name: str) -> None:
    s = shop
    current = take(db, s)
    ddl(tabrion_postgres_url, f'CREATE TABLE {s}."{name}" (version_num varchar(32))')
    with_marker = take(db, s)
    with pytest.raises(PlanRefused) as caught:
        run(db, s, with_marker, with_note(with_marker, s))
    assert caught.value.details == {"reason": "migration_managed", "markers": name.lower()}
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(with_marker)
    assert current != with_marker


def test_risk_not_allowed_refused(db: Database, shop: str) -> None:
    s = shop
    current = take(db, s)
    target = with_note(current, s)
    with pytest.raises(PlanRefused) as caught:
        run(db, s, current, target, allow=[tp.RiskClass.ADDITIVE])
    assert caught.value.details["reason"] == "risk_not_allowed"
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)


# --- CONCURRENTLY ------------------------------------------------------------------------------------------------


def test_concurrent_index_steps_run_alone_in_plan_order(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    s = shop
    current = take(db, s)
    target = with_changed_index(with_note(current, s, default=None), s)
    preview = tp.plan(current, target, concurrent_indexes=True)
    assert [(step.operation, step.transactional) for step in preview.steps] == [
        ("drop_index", False),
        ("add_column", True),
        ("create_index", False),
    ]
    dry = run(db, s, current, target, concurrent_indexes=True, dry_run=True)
    assert (dry.steps_run, dry.untested_steps) == (1, (0, 2))
    assert ts.fingerprint(take(db, s)) == ts.fingerprint(current)
    result = run(db, s, current, target, concurrent_indexes=True)
    assert result.steps_run == 3
    after = take(db, s)
    assert ts.compare(target, after) == ()
    assert result.fingerprint_after == ts.fingerprint(after)
    assert [i.valid for i in table(after, f"{s}.parts").indexes] == [True]


def test_only_concurrent_steps(db: Database, shop: str) -> None:
    s = shop
    current = take(db, s)
    target = with_changed_index(current, s)
    result = run(db, s, current, target, concurrent_indexes=True)
    assert result.steps_run == 2
    assert ts.compare(target, take(db, s)) == ()


def test_failing_non_transactional_step_reports_partial(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    """A unique index on duplicate values, created ``CONCURRENTLY`` after the committed column step: the column
    stays, the step fails, and the state is ``partial`` (PostgreSQL leaves the failed index behind, invalid)."""
    s = shop
    ddl(tabrion_postgres_url, f"INSERT INTO {s}.parts VALUES (3, 'b%:x', 1)")  # 'B%:X' twice after the trigger
    current = take(db, s)
    parts = table(current, f"{s}.parts")
    unique = ts.IndexShape("parts_code_unique", ("code",), unique=True)
    target = replace_table(with_note(current, s, default=None), f"{s}.parts", indexes=(*parts.indexes, unique))
    with pytest.raises(PlanFailed) as caught:
        run(db, s, current, target, concurrent_indexes=True)
    error = caught.value
    assert error.details == {"step": 1, "completed": 1, "state": "partial"}
    assert error.original is not None
    after = take(db, s)
    assert "note" in {c.name for c in table(after, f"{s}.parts").columns}
    assert {(i.name, i.valid) for i in table(after, f"{s}.parts").indexes} == {
        ("parts_code", True),
        ("parts_code_unique", False),
    }


def test_drift_after_leading_concurrent_steps_reports_partial(
    db: Database, shop: str, tabrion_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the leading ``DROP INDEX CONCURRENTLY`` the executor plans again under new locks; a change by another
    session in between stops the run before the transactional steps (state ``partial``: the drop stays)."""
    s = shop
    current = take(db, s)
    target = with_changed_index(with_note(current, s, default=None), s)
    original = tp._Executor.run_alone

    def run_alone(self: Any, step: tp.PlanStep) -> None:
        original(self, step)
        if step.operation == "drop_index":
            ddl(tabrion_postgres_url, f"ALTER TABLE {s}.moves ADD COLUMN extra integer")

    monkeypatch.setattr(tp._Executor, "run_alone", run_alone)
    with pytest.raises(PlanFailed) as caught:
        run(db, s, current, target, concurrent_indexes=True)
    assert caught.value.details == {"step": 1, "completed": 1, "state": "partial", "check": "drift"}
    after = table(take(db, s), f"{s}.parts")
    assert after.indexes == ()
    assert "note" not in {c.name for c in after.columns}
