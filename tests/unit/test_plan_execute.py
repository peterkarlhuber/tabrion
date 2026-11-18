"""The executor of schema change plans on SQLite (TABRION TAB-XW-5, CR-DBX).

Contract I-125; ADR 0015 sections 3, 4 and 10 (D-5.4); the review of #89 (P89-01 foreign keys, P89-03 plan again and
compare with the confirmed hash). ``execute_plan`` takes the target model, never a plan: it plans again inside its
own transaction and runs only the plan whose hash the user confirmed. Every refusal leaves the file as it was; a
failing step or check rolls back. The PostgreSQL side (locks, ``lock_timeout``, ``CONCURRENTLY``) is
``tests/postgres/test_plan_execute_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import dataclasses
import inspect
from pathlib import Path
import pickle
import sqlite3
import typing
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from tabrion import Database, Profile  # noqa: E402
from tabrion import plan as tp  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import (  # noqa: E402
    DatabaseError,
    PlanError,
    PlanFailed,
    PlanMismatch,
    PlanRefused,
    SchemaDrift,
    UsageError,
)

SECRET = "SECRET-4711"

SOURCE = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY, a TEXT, b TEXT)",
    "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent (id) ON DELETE CASCADE)",
    "CREATE TABLE log (id INTEGER PRIMARY KEY, what TEXT)",
    "CREATE TRIGGER child_log AFTER UPDATE ON child BEGIN INSERT INTO log (what) VALUES ('u'); END",
    f"INSERT INTO parent VALUES (1, '{SECRET}', 'x'), (2, NULL, 'y')",  # noqa: S608 - a fixed test value
    "INSERT INTO child VALUES (1, 1), (2, 2)",
)


def make(path: Path, statements: tuple[str, ...] | list[str] = SOURCE) -> None:
    database = Database.from_url(f"sqlite:///{path}")
    try:
        for statement in statements:
            database.execute(statement)
    finally:
        database.close()


def take(db: Database) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, level=2)


def rows(db: Database) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in db.fetch_all("SELECT 'p', id, a, b FROM parent UNION ALL SELECT 'c', id, pid, NULL FROM child")
    ]


def replace_table(model: ts.SchemaSnapshot, name: str, **changes: Any) -> ts.SchemaSnapshot:
    tables = tuple(dataclasses.replace(t, **changes) if t.name == name else t for t in model.tables)
    return dataclasses.replace(model, tables=tables)


def table(model: ts.SchemaSnapshot, name: str) -> ts.TableShape:
    found = model.table(name)
    assert found is not None
    return found


def retyped(model: ts.SchemaSnapshot) -> ts.SchemaSnapshot:
    """``parent.a`` from ``TEXT`` to ``INTEGER``: one rebuild of the table that ``child`` refers to."""
    parent = table(model, "main.parent")
    columns = tuple(dataclasses.replace(c, type="INTEGER") if c.name == "a" else c for c in parent.columns)
    return replace_table(model, "main.parent", columns=columns)


def required(model: ts.SchemaSnapshot) -> ts.SchemaSnapshot:
    """``child.pid`` ``NOT NULL``: a rebuild of ``child`` that recreates its trigger from ``sqlite_schema``."""
    child = table(model, "main.child")
    columns = tuple(dataclasses.replace(c, nullable=False) if c.name == "pid" else c for c in child.columns)
    return replace_table(model, "main.child", columns=columns)


def added(model: ts.SchemaSnapshot) -> ts.SchemaSnapshot:
    """A nullable column on ``child``: one additive in-place step."""
    child = table(model, "main.child")
    return replace_table(model, "main.child", columns=(*child.columns, ts.ColumnShape("note", "TEXT", True)))


@pytest.fixture
def path(tmp_path: Path) -> Path:
    file = tmp_path / "execute.db"
    make(file)
    return file


@pytest.fixture
def db(path: Path) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{path}")
    yield database
    database.close()


def execute(db: Database, target: ts.SchemaSnapshot, current: ts.SchemaSnapshot, **options: Any) -> tp.ExecutionResult:
    """Preview (plan, hash, fingerprint) and run, as Studio does; ``options`` override the confirmation."""
    preview = tp.plan(current=current, target=target, server_version=options.pop("preview_version", None))
    arguments: dict[str, Any] = {
        "plan_hash": preview.plan_hash,
        "expected_fingerprint": ts.fingerprint(current),
        "environment": "dev",
        "allow": preview.risks,
    }
    arguments.update(options)
    return tp.execute_plan(db, target, **arguments)


def unchanged(db: Database, before: ts.SchemaSnapshot, data: list[tuple[Any, ...]]) -> None:
    assert ts.fingerprint(take(db)) == ts.fingerprint(before)
    assert rows(db) == data


# --- D-3: the environment --------------------------------------------------------------------------------------------


def test_environment_is_required_keyword_without_default() -> None:
    parameter = inspect.signature(tp.execute_plan).parameters["environment"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    hints = typing.get_type_hints(tp.execute_plan, localns={"Database": Database})
    assert hints["environment"] == typing.Literal["dev", "test"]


@pytest.mark.parametrize("environment", ["prod", "production", "DEV", "Test", "dev ", "", None, 1, b"dev"])
def test_environment_other_values_refused(db: Database, environment: object) -> None:
    current = take(db)
    data = rows(db)
    with pytest.raises(PlanRefused) as caught:
        execute(db, added(current), current, environment=environment)
    assert caught.value.code == "plan.refused"
    assert caught.value.details == {"reason": "environment"}
    unchanged(db, current, data)


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_dev_and_test_run(db: Database, environment: str) -> None:
    current = take(db)
    target = added(current)
    result = execute(db, target, current, environment=environment)
    assert result.steps_run == 1
    assert result.dry_run is False
    assert result.untested_steps == ()
    assert result.fingerprint_after == ts.fingerprint(take(db))
    assert ts.compare(expected=target, actual=take(db)) == ()


# --- P89-03: plan again, compare with the confirmed hash ----------------------------------------------------------


def test_executor_never_takes_a_plan() -> None:
    """Review of #89, P89-03: no parameter takes a ``MigrationPlan``, its JSON or statements; the target model and the
    confirmed hash are all the executor gets, and it plans again itself."""
    hints = typing.get_type_hints(tp.execute_plan, localns={"Database": Database})
    assert "plan" not in hints
    for name, hint in hints.items():
        assert tp.MigrationPlan not in (hint, *typing.get_args(hint)), name
    assert set(inspect.signature(tp.execute_plan).parameters) == {
        "database",
        "target",
        "plan_hash",
        "expected_fingerprint",
        "environment",
        "allow",
        "source",
        "schemas",
        "server_version",
        "concurrent_indexes",
        "dry_run",
        "lock_timeout",
        "timeout",
    }


def test_swapped_plan_refused(db: Database) -> None:
    """The "swapped plan" test of planning document 15 section 9.4, as the review of #89 (P89-03) words it: the user
    confirmed the hash of one plan, the target held for the execution is another (a table drop): the plan made
    again at execution has another hash and nothing runs."""
    current = take(db)
    data = rows(db)
    confirmed = tp.plan(current=current, target=added(current))
    swapped = dataclasses.replace(current, tables=tuple(t for t in current.tables if t.name != "main.child"))
    with pytest.raises(PlanMismatch) as caught:
        tp.execute_plan(
            db,
            swapped,
            plan_hash=confirmed.plan_hash,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
            allow=list(tp.RiskClass),
        )
    assert caught.value.code == "plan.hash_mismatch"
    assert caught.value.details["expected"] == confirmed.plan_hash
    assert caught.value.details["actual"] == tp.plan(current=current, target=swapped).plan_hash
    unchanged(db, current, data)


def test_edited_hash_refused(db: Database) -> None:
    current = take(db)
    data = rows(db)
    edited = "sha256:" + "0" * 64
    with pytest.raises(PlanMismatch) as caught:
        execute(db, added(current), current, plan_hash=edited)
    assert caught.value.code == "plan.hash_mismatch"
    unchanged(db, current, data)


def test_other_planning_options_change_the_hash(db: Database) -> None:
    """``server_version`` is part of the confirmation: a preview made for an in-place ``DROP COLUMN`` does not run as
    a rebuild, or the other way round."""
    current = take(db)
    parent = table(current, "main.parent")
    target = replace_table(current, "main.parent", columns=parent.columns[:2])
    in_place = tp.plan(current=current, target=target, server_version=(3, 45, 0))
    with pytest.raises(PlanMismatch):
        tp.execute_plan(
            db,
            target,
            plan_hash=in_place.plan_hash,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
            allow=list(tp.RiskClass),
        )
    result = tp.execute_plan(
        db,
        target,
        plan_hash=in_place.plan_hash,
        expected_fingerprint=ts.fingerprint(current),
        environment="dev",
        allow=list(tp.RiskClass),
        server_version=(3, 45, 0),
    )
    assert result.plan_hash == in_place.plan_hash


def test_risk_classes_come_from_the_new_plan(db: Database) -> None:
    current = take(db)
    data = rows(db)
    target = retyped(current)
    with pytest.raises(PlanRefused) as caught:
        execute(db, target, current, allow=[tp.RiskClass.ADDITIVE])
    assert caught.value.details["reason"] == "risk_not_allowed"
    assert caught.value.details["risks"] == "data_changing,destructive,rebuild"
    unchanged(db, current, data)
    with pytest.raises(PlanRefused):  # the default allows additive steps only
        tp.execute_plan(
            db,
            target,
            plan_hash=tp.plan(current=current, target=target).plan_hash,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
        )
    unchanged(db, current, data)


# --- drift ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        ["ALTER TABLE log ADD COLUMN extra TEXT"],
        ["DROP TRIGGER child_log"],
        # the trigger body changes, its name and table stay
        [
            "DROP TRIGGER child_log",
            "CREATE TRIGGER child_log AFTER UPDATE ON child BEGIN DELETE FROM log; END",
        ],
        ["CREATE INDEX log_what ON log (what)"],
        ["CREATE VIEW v AS SELECT id FROM log"],
    ],
)
def test_drift_between_preview_and_execution_refused(path: Path, db: Database, change: list[str]) -> None:
    current = take(db)
    target = added(current)
    preview = tp.plan(current=current, target=target)
    with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as connection:
        for statement in change:
            connection.execute(statement)
    drifted = take(db)
    data = rows(db)
    with pytest.raises(SchemaDrift) as caught:
        tp.execute_plan(
            db,
            target,
            plan_hash=preview.plan_hash,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
            allow=preview.risks,
            source=current,
        )
    error = caught.value
    assert isinstance(error, PlanError)
    assert error.code == "plan.drift"
    assert error.details["expected"] == ts.fingerprint(current)
    assert error.details["actual"] == ts.fingerprint(drifted)
    assert error.details["differences"] == len(error.differences)
    assert error.differences == ts.compare(expected=current, actual=drifted)
    unchanged(db, drifted, data)


def test_drift_without_source_names_no_differences(path: Path, db: Database) -> None:
    current = take(db)
    target = added(current)
    preview = tp.plan(current=current, target=target)
    with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("DROP TRIGGER child_log")
    with pytest.raises(SchemaDrift) as caught:
        execute(db, target, current, plan_hash=preview.plan_hash)
    assert caught.value.differences == ()
    assert caught.value.details["differences"] == 0


def test_source_must_have_the_expected_fingerprint(db: Database) -> None:
    current = take(db)
    with pytest.raises(PlanMismatch) as caught:
        execute(db, added(current), current, source=added(current))
    assert caught.value.code == "plan.fingerprint_mismatch"
    assert caught.value.details == {"field": "source"}
    with pytest.raises(PlanMismatch) as caught:
        execute(db, added(current), current, expected_fingerprint="v1:sha256:" + "0" * 64)
    assert caught.value.details == {"field": "expected_fingerprint"}


def test_schema_drift_pickles_with_its_differences(path: Path, db: Database) -> None:
    current = take(db)
    with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("DROP TRIGGER child_log")
    with pytest.raises(SchemaDrift) as caught:
        execute(db, added(current), current, source=current)
    copy = pickle.loads(pickle.dumps(caught.value))  # noqa: S301 - our own bytes
    assert type(copy) is SchemaDrift
    assert copy.code == caught.value.code
    assert copy.differences == caught.value.differences
    assert dict(copy.details) == dict(caught.value.details)


# --- migration management, dialect -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "markers"),
    [
        ("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)", "alembic_version"),
        ("CREATE TABLE NODESTRO_SCHEMA_MIGRATIONS (v INTEGER)", "nodestro_schema_migrations"),
        ("PRAGMA user_version = 3", "sqlite_user_version"),
    ],
)
def test_migration_managed_database_refused(path: Path, statement: str, markers: str) -> None:
    with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute(statement)
    database = Database.from_url(f"sqlite:///{path}")
    try:
        current = take(database)
        data = rows(database)
        with pytest.raises(PlanRefused) as caught:
            execute(database, added(current), current)
        assert caught.value.details == {"reason": "migration_managed", "markers": markers}
        unchanged(database, current, data)
    finally:
        database.close()


def test_dialect_mismatch_refused(db: Database) -> None:
    current = take(db)
    other = dataclasses.replace(current, dialect="postgresql", views=())
    with pytest.raises(PlanRefused) as caught:
        execute(db, other, current)
    assert caught.value.details == {"reason": "dialect_mismatch"}


def test_level_1_target_refused(db: Database) -> None:
    with db.inspector() as inspector:
        level1 = ts.snapshot(inspector)
    with pytest.raises(UsageError) as caught:
        execute(db, level1, take(db))
    assert caught.value.code == "plan.level_required"


# --- foreign keys, checks and rollback (review of #89, P89-01) ---------------------------------------------------


def test_rebuild_with_foreign_keys_on_keeps_child_rows(db: Database) -> None:
    """The profile turns foreign keys on; the executor turns them off before ``BEGIN``, so the ``DROP TABLE`` of the
    rebuild deletes no child row through ``ON DELETE CASCADE``, and turns them on again afterwards."""
    assert db.fetch_scalar("PRAGMA foreign_keys") == 1
    current = take(db)
    target = retyped(current)
    result = execute(db, target, current)
    assert result.steps_run == 1
    assert ts.compare(expected=target, actual=take(db)) == ()
    assert db.fetch_scalar("SELECT count(*) FROM child") == 2
    assert db.fetch_scalar("PRAGMA foreign_keys") == 1
    assert db.fetch_scalar("PRAGMA trusted_schema") == 0
    db.execute("UPDATE child SET pid = pid WHERE id = 1")  # the trigger on child is untouched and still fires
    assert db.fetch_scalar("SELECT count(*) FROM log") == 1


def test_foreign_keys_on_refused_no_row_lost(path: Path) -> None:
    """Review of #89, P89-01: an authorizer that ignores ``PRAGMA foreign_keys = OFF`` keeps them on; the executor
    refuses to start (reason ``foreign_keys_on``) and no row is lost."""

    def ignore_foreign_keys_off(action: int, arg1: str | None, arg2: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "foreign_keys" and arg2 is not None:
            return sqlite3.SQLITE_IGNORE
        return sqlite3.SQLITE_OK

    database = Database.from_url(f"sqlite:///{path}")
    sqlalchemy.event.listen(
        database.engine, "connect", lambda dbapi, _record: dbapi.set_authorizer(ignore_foreign_keys_off)
    )
    try:
        current = take(database)
        data = rows(database)
        for target in (retyped(current), added(current)):
            with pytest.raises(PlanRefused) as caught:
                execute(database, target, current)
            assert caught.value.details == {"reason": "foreign_keys_on"}
            unchanged(database, current, data)
        assert database.fetch_scalar("SELECT count(*) FROM child") == 2
    finally:
        database.close()


def test_foreign_key_violation_rolls_back(path: Path) -> None:
    """A rebuild that adds a foreign key to rows without a parent: ``foreign_key_check`` before the commit finds them,
    the transaction rolls back (state ``rolled_back``, ``violations`` a count)."""
    make(
        path, ["CREATE TABLE orphan (id INTEGER PRIMARY KEY, pid INTEGER)", "INSERT INTO orphan VALUES (1, 7), (2, 8)"]
    )
    database = Database.from_url(f"sqlite:///{path}")
    try:
        current = take(database)
        data = rows(database)
        key = ts.ForeignKeyShape(name=None, columns=("pid",), referred_table="main.parent", referred_columns=("id",))
        target = replace_table(current, "main.orphan", foreign_keys=(key,))
        with pytest.raises(PlanFailed) as caught:
            execute(database, target, current)
        error = caught.value
        assert error.code == "plan.failed"
        assert error.details == {
            "step": 0,
            "completed": 0,
            "state": "rolled_back",
            "check": "foreign_key_check",
            "violations": 2,
        }
        unchanged(database, current, data)
        assert database.fetch_scalar("SELECT count(*) FROM orphan") == 2
        assert database.fetch_scalar("PRAGMA foreign_keys") == 1
    finally:
        database.close()


def test_failing_step_rolls_back_and_keeps_data(db: Database) -> None:
    """``NOT NULL`` on a column with a ``NULL``: the rebuild's copy fails, nothing of the plan stays; the error says
    which step and carries no value."""
    current = take(db)
    data = rows(db)
    parent = table(current, "main.parent")
    columns = tuple(dataclasses.replace(c, nullable=False) if c.name == "a" else c for c in parent.columns)
    target = replace_table(current, "main.parent", columns=columns)
    with pytest.raises(PlanFailed) as caught:
        execute(db, target, current)
    error = caught.value
    assert error.details == {"step": 0, "completed": 0, "state": "rolled_back"}
    assert error.original is not None
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert SECRET not in str(error)
    assert SECRET not in repr(error)
    unchanged(db, current, data)
    assert db.fetch_scalar("PRAGMA foreign_keys") == 1


def test_dry_run_leaves_the_file_unchanged(db: Database) -> None:
    current = take(db)
    data = rows(db)
    target = retyped(current)
    result = execute(db, target, current, dry_run=True)
    assert result == tp.ExecutionResult(tp.plan(current=current, target=target).plan_hash, True, 1, (), None)
    unchanged(db, current, data)
    assert db.fetch_scalar("PRAGMA foreign_keys") == 1


def test_dry_run_reports_a_failing_step(db: Database) -> None:
    current = take(db)
    data = rows(db)
    parent = table(current, "main.parent")
    columns = tuple(dataclasses.replace(c, nullable=False) if c.name == "a" else c for c in parent.columns)
    with pytest.raises(PlanFailed) as caught:
        execute(db, replace_table(current, "main.parent", columns=columns), current, dry_run=True)
    assert caught.value.details["state"] == "rolled_back"
    unchanged(db, current, data)


def test_an_empty_plan_runs(db: Database) -> None:
    current = take(db)
    result = execute(db, current, current)
    assert result.steps_run == 0
    assert result.fingerprint_after == ts.fingerprint(current)


# --- K-1 at execution: defensive mode ----------------------------------------------------------------------------


HAS_DEFENSIVE = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None) is not None  # Python >= 3.12


def test_without_defensive_mode_a_trigger_text_is_refused(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Python 3.11 has no ``Connection.setconfig``: a plan that recreates a trigger from ``sqlite_schema`` (K-1) is
    refused there; plans without one (an added column, a rebuild of a table without triggers) run."""
    monkeypatch.delattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", raising=False)
    current = take(db)
    data = rows(db)
    with pytest.raises(PlanRefused) as caught:
        execute(db, required(current), current)
    assert caught.value.details == {"reason": "engine_unsupported", "feature": "sqlite_defensive"}
    unchanged(db, current, data)
    assert execute(db, retyped(current), current).steps_run == 1
    current = take(db)
    assert execute(db, added(current), current).steps_run == 1


def test_rebuild_with_a_trigger_by_python_version(db: Database) -> None:
    """The version split, explicitly: with defensive mode (Python >= 3.12) the rebuild of ``child`` runs and recreates
    its trigger; without it (Python 3.11) the same plan is refused before ``BEGIN`` and nothing changes."""
    current = take(db)
    data = rows(db)
    target = required(current)
    if not HAS_DEFENSIVE:
        with pytest.raises(PlanRefused) as caught:
            execute(db, target, current)
        assert caught.value.details == {"reason": "engine_unsupported", "feature": "sqlite_defensive"}
        unchanged(db, current, data)
        return
    assert execute(db, target, current).steps_run == 1
    assert ts.compare(expected=target, actual=take(db)) == ()
    assert rows(db) == data
    db.execute("UPDATE child SET pid = pid WHERE id = 1")  # the trigger was recreated
    assert db.fetch_scalar("SELECT count(*) FROM log") == 1


def test_connection_settings_restored(db: Database) -> None:
    """``foreign_keys``, ``trusted_schema`` and defensive mode are back as they were after a run and after a
    failure."""

    def settings() -> tuple[Any, ...]:
        with db.transaction() as transaction:
            raw: Any = transaction.connection.connection.dbapi_connection
            flag = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
            defensive = raw.getconfig(flag) if flag is not None else None
            return (
                transaction.fetch_scalar("PRAGMA foreign_keys"),
                transaction.fetch_scalar("PRAGMA trusted_schema"),
                defensive,
            )

    before = settings()
    current = take(db)
    execute(db, retyped(current), current)
    assert settings() == before
    current = take(db)
    with pytest.raises(PlanMismatch):
        execute(db, added(current), current, plan_hash="sha256:" + "1" * 64)
    assert settings() == before


# --- arguments ---------------------------------------------------------------------------------------------------


def test_nested_transaction_refused(db: Database) -> None:
    current = take(db)
    with db.transaction(), pytest.raises(UsageError) as caught:
        execute(db, added(current), current)
    assert caught.value.code == "database.nested_transaction"


def test_read_only_profile_refused(path: Path) -> None:
    database = Database.from_url(f"sqlite:///{path}", profile=Profile(read_only=True))
    try:
        current = take(database)
        with pytest.raises(UsageError) as caught:
            execute(database, added(current), current)
        assert caught.value.code == "database.read_only"
    finally:
        database.close()


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"allow": "additive"}, TypeError),
        ({"allow": [1]}, TypeError),
        ({"allow": ["everything"]}, ValueError),
        ({"plan_hash": None}, TypeError),
        ({"expected_fingerprint": 1}, TypeError),
        ({"dry_run": "yes"}, TypeError),
        ({"lock_timeout": 0}, ValueError),
        ({"lock_timeout": -1.0}, ValueError),
        ({"lock_timeout": float("nan")}, ValueError),
        ({"lock_timeout": 3601}, ValueError),
        ({"lock_timeout": True}, TypeError),
        ({"timeout": 0}, ValueError),
        ({"timeout": "5"}, TypeError),
        ({"schemas": "main"}, TypeError),
        ({"schemas": [1]}, TypeError),
        ({"source": "x"}, TypeError),
    ],
)
def test_arguments_checked_before_anything_runs(db: Database, options: dict[str, Any], error: type[Exception]) -> None:
    current = take(db)
    data = rows(db)
    with pytest.raises(error):
        execute(db, added(current), current, **options)
    unchanged(db, current, data)


def test_database_must_be_a_tabrion_database(db: Database) -> None:
    current = take(db)
    with pytest.raises(TypeError):
        tp.execute_plan(
            db.engine,  # type: ignore[arg-type]
            current,
            plan_hash="sha256:" + "0" * 64,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
        )
    with pytest.raises(TypeError):
        tp.execute_plan(
            db,
            current.to_json(),  # type: ignore[arg-type]
            plan_hash="sha256:" + "0" * 64,
            expected_fingerprint=ts.fingerprint(current),
            environment="dev",
        )


def test_schemas_select_the_snapshot(tmp_path: Path) -> None:
    """``schemas`` must be those of the preview's snapshot; an attached database counts like ``main``."""
    file = tmp_path / "main.db"
    make(file, ["CREATE TABLE a (id INTEGER PRIMARY KEY)"])
    database = Database.from_url(f"sqlite:///{file}")
    try:
        current = take(database)
        target = replace_table(
            current, "main.a", columns=(*table(current, "main.a").columns, ts.ColumnShape("n", "TEXT", True))
        )
        with database.inspector() as inspector:
            both = ts.snapshot(inspector, level=2, schemas=["main"])
        assert both == current
        result = execute(database, target, current, schemas=["main"])
        assert result.steps_run == 1
    finally:
        database.close()


def test_a_denied_pragma_refuses_before_begin(path: Path) -> None:
    """An authorizer that denies ``PRAGMA foreign_keys = OFF``: the preparation before ``BEGIN`` fails with the
    translated error, the connection is discarded, nothing runs."""

    def deny_foreign_keys_off(action: int, arg1: str | None, arg2: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "foreign_keys" and arg2 is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    database = Database.from_url(f"sqlite:///{path}")
    sqlalchemy.event.listen(
        database.engine, "connect", lambda dbapi, _record: dbapi.set_authorizer(deny_foreign_keys_off)
    )
    try:
        current = take(database)
        data = rows(database)
        with pytest.raises(DatabaseError) as caught:
            execute(database, added(current), current)
        assert caught.value.__cause__ is None
        unchanged(database, current, data)
    finally:
        database.close()


def test_autocommit_hook_is_postgresql_only(db: Database) -> None:
    with pytest.raises(UsageError) as caught:
        db._autocommit("SELECT 1", timeout=None, lock_timeout=1.0)
    assert caught.value.code == "database.unsupported_dialect"
