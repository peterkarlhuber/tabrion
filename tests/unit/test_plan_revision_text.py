"""Alembic revision text of a schema change plan, and K-3 (TABRION TAB-XW-4, CR-DBX).

Contract I-124 g; ADR 0015 section 2 and K-3 (section 9): the module docstring is a fixed text, every caller text
(``revision``, ``down_revision``, the message, each statement) is a Python string literal, every output goes through
``ast.parse``, and ``revision_text`` touches no file and imports nothing (audit-hook probe). The revisions run here
through Alembic's ``Operations`` on a connection the test opens, never through an ``env.py``; Tabrion itself never
runs one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import dataclasses
from pathlib import Path
import sqlite3
import sys
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from tabrion import plan as tp  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import UsageError  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
SECRET = "SECRET-4711"


def column(name: str, type_: str = "INTEGER", nullable: bool = True, default: str | None = None) -> ts.ColumnShape:
    return ts.ColumnShape(name, type_, nullable, default)


PARENT = ts.TableShape("public.parts", (column("id", nullable=False), column("code", "VARCHAR(10)")), ("id",))
CHILD = ts.TableShape(
    "public.moves",
    (column("id", nullable=False), column("part"), column("note", "TEXT", True, "'a:b'")),
    ("id",),
    indexes=(ts.IndexShape("moves_note", ("note",)),),
)


def pg_plan() -> tp.MigrationPlan:
    """A PostgreSQL plan with every kind of line: a table, a key, a type change and concurrent indexes."""
    current = ts.SchemaSnapshot("postgresql", (PARENT, CHILD), level=2)
    child = dataclasses.replace(
        CHILD,
        columns=(*CHILD.columns, column("qty", "INTEGER", False, "0")),
        foreign_keys=(ts.ForeignKeyShape("moves_part", ("part",), "public.parts", ("id",)),),
        indexes=(ts.IndexShape("moves_part_ix", ("part",)),),
    )
    parent = dataclasses.replace(PARENT, columns=(PARENT.columns[0], column("code", "VARCHAR(20)")))
    log = ts.TableShape("public.log", (column("id", nullable=False), column("what", "TEXT")), ("id",))
    target = ts.SchemaSnapshot("postgresql", (parent, child, log), level=2)
    return tp.plan(current, target, concurrent_indexes=True)


def sqlite_plan() -> tp.MigrationPlan:
    table = ts.TableShape(
        "main.parts",
        (column("id", nullable=False), column("code", "VARCHAR(10)")),
        ("id",),
        triggers=(ts.TriggerShape("parts_tr", "CREATE TRIGGER parts_tr AFTER INSERT ON parts BEGIN SELECT 1; END"),),
    )
    old = ts.TableShape("main.old", (column("id"),), ())
    current = ts.SchemaSnapshot("sqlite", (old, table), level=2)
    changed = dataclasses.replace(table, columns=(table.columns[0], column("code", "TEXT", False)))
    return tp.plan(current, ts.SchemaSnapshot("sqlite", (changed,), level=2))


def render(plan: tp.MigrationPlan, **kwargs: Any) -> str:
    options: dict[str, Any] = {"revision": "a1b2c3", "down_revision": "f0e1d2", "message": "add qty"}
    options.update(kwargs)
    text = tp.revision_text(plan, **options)
    check_literals_only(text, plan)
    return text


ALLOWED_NAMES = {"op", "sa", "dialect", "RuntimeError", "NotImplementedError"}
ALLOWED_ATTRIBUTES = {
    "execute",
    "text",
    "get_context",
    "dialect",
    "name",
    "autocommit_block",
    # the SQLite foreign key guard and check (review of #89, P89-01): fixed pragma texts
    "get_bind",
    "exec_driver_sql",
    "scalar",
    "first",
}
FIXED_DRIVER_SQL = {"PRAGMA foreign_keys", "PRAGMA foreign_key_check"}
MODULE_NAMES = (
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "message",
    "dialect",
    "plan_hash",
    "source_fingerprint",
    "target_fingerprint",
)


def check_literals_only(text: str, plan: tp.MigrationPlan) -> ast.Module:
    """K-3: the text parses; the docstring is the fixed one; the module holds only the two imports, assignments of
    literals and the two functions; the functions use only the fixed names and calls, and every statement is a string
    literal that gives back the plan's SQL."""
    module = ast.parse(text)
    assert ast.get_docstring(module, clean=False) == ast.literal_eval(tp._DOCSTRING)
    body = module.body[1:]
    imports = [node for node in body if isinstance(node, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(node) for node in imports] == ["from alembic import op", "import sqlalchemy as sa"]
    assigns = [node for node in body if isinstance(node, ast.Assign)]
    assert [node.targets[0].id for node in assigns] == list(MODULE_NAMES)  # type: ignore[attr-defined]
    assert all(isinstance(node.value, ast.Constant) for node in assigns)
    functions = [node for node in body if isinstance(node, ast.FunctionDef)]
    assert [f.name for f in functions] == ["upgrade", "downgrade"]
    assert len(imports) + len(assigns) + len(functions) == len(body)
    texts = []
    for function in functions:
        for node in ast.walk(function):
            assert not isinstance(node, ast.JoinedStr | ast.BinOp | ast.Lambda | ast.Import | ast.ImportFrom)
            if isinstance(node, ast.Name):
                assert node.id in ALLOWED_NAMES, node.id
            if isinstance(node, ast.Attribute):
                assert node.attr in ALLOWED_ATTRIBUTES, node.attr
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "op.get_bind().exec_driver_sql":
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                assert node.args[0].value in FIXED_DRIVER_SQL
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "sa.text":
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and not node.keywords
                texts.append((node.lineno, sqlalchemy.text(node.args[0].value).compile().string))
    assert [sql for _, sql in sorted(texts)] == [s.sql for step in plan.steps for s in step.statements]
    return module


def module_values(text: str) -> dict[str, Any]:
    return {node.targets[0].id: node.value.value for node in ast.parse(text).body[1:] if isinstance(node, ast.Assign)}  # type: ignore[attr-defined]


# --- golden files ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "make"), [("postgresql", pg_plan), ("sqlite", sqlite_plan)])
def test_xw4_revision_text_golden(name: str, make: Any) -> None:
    """The whole text for a fixed plan; ``tests/golden/plan_revision_<dialect>.txt``."""
    text = render(make())
    assert text == (GOLDEN / f"plan_revision_{name}.txt").read_text(encoding="utf-8")
    assert text.isascii()


def test_xw4_revision_values(tmp_path: Path) -> None:
    plan = pg_plan()
    values = module_values(render(plan, down_revision=None, message=f"msg {SECRET}"))
    assert values == {
        "revision": "a1b2c3",
        "down_revision": None,
        "branch_labels": None,
        "depends_on": None,
        "message": f"msg {SECRET}",
        "dialect": "postgresql",
        "plan_hash": plan.plan_hash,
        "source_fingerprint": plan.source_fingerprint,
        "target_fingerprint": plan.target_fingerprint,
    }


# --- K-3 ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        '"""',
        '""" ; import os; os.system("x") ; """',
        "\\",
        "x\\",
        "\r\n",
        "\x00",
        "'''\n\nimport os\n",
        "‮ reversed",
        "\ud800",
        "",
    ],
)
def test_message_cannot_break_docstring(message: str) -> None:
    """K-3: the docstring is fixed; the message is only the literal ``message``, whatever it holds."""
    plan = pg_plan()
    text = render(plan, message=message)
    assert module_values(text)["message"] == message
    assert text.isascii()
    namespace: dict[str, Any] = {}
    exec(compile(text, "revision.py", "exec"), namespace)  # noqa: S102 - the text is the test's own output
    assert namespace["message"] == message and "os" not in namespace


@pytest.mark.parametrize(
    ("revision", "down_revision", "field"),
    [
        ("abc", '"; import os', "down_revision"),
        ('"; import os', None, "revision"),
        ("", None, "revision"),
        ("a" * 65, None, "revision"),
        ("abc", "a" * 65, "down_revision"),
        ("ab-c", None, "revision"),
        ("ab c", None, "revision"),
        ("abc\n", None, "revision"),
        ("äbc", None, "revision"),
        ("abc", "", "down_revision"),
        ("abc", "../x", "down_revision"),
    ],
)
def test_revision_ids_validated(revision: str, down_revision: str | None, field: str) -> None:
    """K-3: ``revision`` and ``down_revision`` match ``[0-9a-zA-Z_]{1,64}`` (``down_revision`` may be ``None``)."""
    with pytest.raises(UsageError) as info:
        tp.revision_text(pg_plan(), revision=revision, down_revision=down_revision, message="m")
    assert info.value.code == "plan.invalid_revision"
    assert info.value.details == {"field": field}
    assert "import" not in str(info.value)
    assert tp.revision_text(pg_plan(), revision="A_z9" * 16, down_revision="0" * 64, message="m")


def test_revision_ids_type_checked() -> None:
    with pytest.raises(UsageError):
        tp.revision_text(pg_plan(), revision=1, down_revision=None, message="m")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tp.revision_text(pg_plan(), revision="a", down_revision=None, message=b"m")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tp.revision_text("plan", revision="a", down_revision=None, message="m")  # type: ignore[arg-type]


def test_revision_text_ast_has_only_literals() -> None:
    """K-3: every output through ``ast.parse``: only literals at the places of caller text, for both dialects, an
    empty plan, a hostile table name and a hostile message."""
    hostile = ts.TableShape('public.x"); import os; ("', (column("id"),), ())
    plans = [
        pg_plan(),
        sqlite_plan(),
        tp.plan(ts.SchemaSnapshot("sqlite", (), level=2), ts.SchemaSnapshot("sqlite", (), level=2)),
        tp.plan(ts.SchemaSnapshot("postgresql", (), level=2), ts.SchemaSnapshot("postgresql", (hostile,), level=2)),
    ]
    for plan in plans:
        for message in ("m", '""" + __import__("os").getcwd() + """', "x');\nimport os\n#"):
            module = check_literals_only(
                tp.revision_text(plan, revision="r1", down_revision=None, message=message), plan
            )
            assert not any(isinstance(n, ast.Name) and n.id in ("os", "__import__") for n in ast.walk(module))


def test_xw4_injection_in_a_table_name_is_inert(tmp_path: Path) -> None:
    """The table ``x"); import os; ("`` is quoted by the dialect inside a string literal; loading the revision runs
    nothing but the assignments."""
    hostile = ts.TableShape('public.x"); import os; ("', (column("id"),), ())
    plan = tp.plan(ts.SchemaSnapshot("postgresql", (), level=2), ts.SchemaSnapshot("postgresql", (hostile,), level=2))
    text = render(plan)
    assert 'CREATE TABLE public."x""); import os; (""" (' in plan.steps[0].statements[0].sql
    namespace: dict[str, Any] = {}
    exec(compile(text, "revision.py", "exec"), namespace)  # noqa: S102 - the text is the test's own output
    assert "os" not in namespace
    assert set(namespace) - {"__builtins__", "__doc__"} == {"op", "sa", "upgrade", "downgrade", *MODULE_NAMES}


@pytest.fixture
def audit() -> Iterator[list[tuple[str, tuple[Any, ...]]]]:
    """Every audit event raised in this thread while the fixture is active (``sys.addaudithook`` cannot be removed,
    so the hook is installed once and records only while a probe runs)."""
    events: list[tuple[str, tuple[Any, ...]]] = []
    _RECORDER["list"] = events
    if not _RECORDER.get("installed"):
        sys.addaudithook(_record)
        _RECORDER["installed"] = True
    try:
        yield events
    finally:
        _RECORDER["events"] = None


_RECORDER: dict[str, Any] = {}


def _record(event: str, args: tuple[Any, ...]) -> None:
    events = _RECORDER.get("events")
    if events is not None:
        events.append((event, args))


def test_xw4_revision_text_touches_no_file_and_imports_nothing(audit: list[tuple[str, tuple[Any, ...]]]) -> None:
    """ADR 0015 section 2, Verification: ``revision_text`` reads no file and no directory, imports no module (no
    ``env.py``, no project module, no ``alembic.command``), starts no process and compiles no code."""
    plan = pg_plan()
    _RECORDER["events"] = audit  # from here on: pytest itself sets PYTEST_CURRENT_TEST before the test
    text = tp.revision_text(plan, revision="r1", down_revision=None, message="m")
    _RECORDER["events"] = None
    assert text
    touched = [
        event
        for event, _ in audit
        if event in ("open", "import", "exec", "compile", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn")
        or event.startswith(("os.", "shutil.", "glob.", "pathlib.", "sqlite3."))
    ]
    assert touched == []
    assert not any("env" in str(args) for _, args in audit)
    # negative probe: the hook does see a file access and an import
    _RECORDER["events"] = audit
    with open(__file__, encoding="utf-8") as handle:
        handle.read(1)
    sys.modules.pop("colorsys", None)
    __import__("colorsys")
    _RECORDER["events"] = None
    assert {"open", "import"} <= {event for event, _ in audit}


# --- running a revision (the test runs it; Tabrion never does) -------------------------------------------------------


def run_revision(connection: Any, text: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    namespace: dict[str, Any] = {}
    exec(compile(text, "revision.py", "exec"), namespace)  # noqa: S102 - the text is the test's own output
    with Operations.context(MigrationContext.configure(connection)):
        namespace["upgrade"]()


def test_xw4_revision_runs_on_sqlite_and_reaches_the_target(tmp_path: Path) -> None:
    """The revision of a SQLite plan (a rebuild with a trigger, a dropped table), run through Alembic's operations
    with foreign keys off, gives the target fingerprint; the colon in a literal survives ``sa.text``."""
    file = tmp_path / "rev.db"
    with sqlite3.connect(file) as connection:
        connection.execute("CREATE TABLE old (id INTEGER)")
        connection.execute("CREATE TABLE parts (id INTEGER NOT NULL, code VARCHAR(10), PRIMARY KEY (id))")
        connection.execute("CREATE TRIGGER parts_tr AFTER INSERT ON parts BEGIN SELECT 1; END")
        connection.execute("INSERT INTO parts VALUES (1, 'a:b')")
    connection.close()
    engine = sqlalchemy.create_engine(f"sqlite:///{file}")
    try:
        with engine.connect() as connection:
            current = ts.snapshot(sqlalchemy.inspect(connection), level=2)
        parts = current.table("main.parts")
        assert parts is not None and len(parts.triggers) == 1
        target = ts.SchemaSnapshot(
            "sqlite",
            (dataclasses.replace(parts, columns=(parts.columns[0], column("code", "TEXT", False, "'x:y'"))),),
            level=2,
        )
        plan = tp.plan(current, target)
        assert [s.operation for s in plan.steps] == ["rebuild_table", "drop_table"]
        text = render(plan)
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.commit()
            with connection.begin():
                run_revision(connection, text)
            after = ts.snapshot(sqlalchemy.inspect(connection), level=2)
            assert ts.fingerprint(after) == plan.target_fingerprint
            assert connection.exec_driver_sql("SELECT code FROM parts").scalar() == "a:b"
    finally:
        engine.dispose()


def test_xw4_revision_refuses_another_engine(tmp_path: Path) -> None:
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'other.db'}")
    try:
        with engine.connect() as connection, pytest.raises(RuntimeError, match="another database engine"):
            run_revision(connection, render(pg_plan()))
    finally:
        engine.dispose()


def test_xw4_downgrade_is_not_written() -> None:
    namespace: dict[str, Any] = {}
    exec(compile(render(pg_plan()), "revision.py", "exec"), namespace)  # noqa: S102 - the test's own output
    with pytest.raises(NotImplementedError):
        namespace["downgrade"]()


# --- review of #89, P89-01: foreign keys around a SQLite rebuild ----------------------------------------------------

CASCADE = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY, a TEXT, b TEXT)",
    "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent (id) ON DELETE CASCADE)",
    "INSERT INTO parent VALUES (1, 'x', 'y')",
    "INSERT INTO child VALUES (1, 1), (2, 1)",
)


def cascade_database(path: Path, *, foreign_keys: bool) -> Any:
    """A layer-B ``Database`` (``BEGIN`` really opens a transaction, so DDL rolls back) with the reviewer's tables."""
    from tabrion import Database, Profile

    base = Profile()
    profile = dataclasses.replace(base, sqlite=dataclasses.replace(base.sqlite, foreign_keys=foreign_keys))
    database = Database.from_url(f"sqlite:///{path}", profile=profile)
    if not path.with_suffix(".seeded").exists():
        for statement in CASCADE:
            database.execute(statement)
        path.with_suffix(".seeded").touch()
    return database


def retyped_parent_plan(database: Any) -> tp.MigrationPlan:
    with database.inspector() as inspector:
        current = ts.snapshot(inspector, level=2, tables=["main.parent"])
    parent = current.tables[0]
    columns = (parent.columns[0], dataclasses.replace(parent.columns[1], type="VARCHAR(5)"), parent.columns[2])
    return tp.plan(current, dataclasses.replace(current, tables=(dataclasses.replace(parent, columns=columns),)))


def upgrade_in_one_transaction(database: Any, text: str) -> None:
    with database.engine.connect() as connection, connection.begin():
        run_revision(connection, text)


def test_xw4_rebuild_with_foreign_keys_on_loses_child_rows_without_the_guard(tmp_path: Path) -> None:
    """The hazard of P89-01, reproduced: the plan's statements, run in one transaction with foreign keys on, commit
    and delete every child row (``DROP TABLE parent`` deletes through ``ON DELETE CASCADE``)."""
    database = cascade_database(tmp_path / "hazard.db", foreign_keys=True)
    try:
        plan = retyped_parent_plan(database)
        assert [s.operation for s in plan.steps] == ["rebuild_table"]
        with database.engine.connect() as connection:
            with connection.begin():
                for statement in plan.steps[0].statements:
                    connection.exec_driver_sql(statement.sql)
            assert connection.exec_driver_sql("SELECT count(*) FROM child").scalar() == 0
    finally:
        database.close()


def test_xw4_revision_refuses_to_start_with_foreign_keys_on(tmp_path: Path) -> None:
    """P89-01: the revision's ``upgrade()`` refuses before the first statement while foreign keys are on; no row is
    lost."""
    database = cascade_database(tmp_path / "guard.db", foreign_keys=True)
    try:
        text = render(retyped_parent_plan(database))
        with pytest.raises(RuntimeError, match="foreign keys are on"):
            upgrade_in_one_transaction(database, text)
        assert database.fetch_scalar("SELECT count(*) FROM child") == 2
        assert database.fetch_scalar("SELECT type FROM pragma_table_info('parent') WHERE name = 'a'") == "TEXT"
    finally:
        database.close()


def test_xw4_revision_with_foreign_keys_off_keeps_child_rows(tmp_path: Path) -> None:
    path = tmp_path / "off.db"
    database = cascade_database(path, foreign_keys=False)
    try:
        plan = retyped_parent_plan(database)
        upgrade_in_one_transaction(database, render(plan))
        assert database.fetch_scalar("SELECT count(*) FROM child") == 2
        with database.inspector() as inspector:
            after = ts.snapshot(inspector, level=2, tables=["main.parent"])
        assert ts.fingerprint(after) == plan.target_fingerprint
    finally:
        database.close()


def test_xw4_revision_foreign_key_check_rolls_back(tmp_path: Path) -> None:
    """P89-01: ``PRAGMA foreign_key_check`` at the end of ``upgrade()``: a plan that drops a table another table still
    refers to (the snapshot did not hold the child) raises, and the transaction rolls the drop back."""
    database = cascade_database(tmp_path / "check.db", foreign_keys=False)
    try:
        with database.inspector() as inspector:
            current = ts.snapshot(inspector, level=2, tables=["main.parent"])
        plan = tp.plan(current, dataclasses.replace(current, tables=()))
        assert [s.operation for s in plan.steps] == ["drop_table"]
        with pytest.raises(RuntimeError, match="foreign key refers to a missing row"):
            upgrade_in_one_transaction(database, render(plan))
        assert database.fetch_scalar("SELECT count(*) FROM parent") == 1
        assert database.fetch_scalar("SELECT count(*) FROM child") == 2
    finally:
        database.close()


def test_xw4_postgres_and_in_place_revisions_have_no_foreign_key_guard() -> None:
    assert "PRAGMA" not in render(pg_plan())
    current = ts.SchemaSnapshot("sqlite", (ts.TableShape("main.t", (column("id", nullable=False),), ("id",)),), level=2)
    added = dataclasses.replace(
        current, tables=(dataclasses.replace(current.tables[0], columns=(*current.tables[0].columns, column("v"))),)
    )
    assert "PRAGMA" not in render(tp.plan(current, added, server_version=(3, 45, 0)))
