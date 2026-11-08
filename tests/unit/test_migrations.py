"""``tabrion.migrations`` on SQLite (TAB-019): TAB-T-M01..M08 (contract section 5.5, implementation note I-57).

M01 fresh database, M02 current, M03 pending, M04 unknown newer revision, M05 several heads, merges and
``depends_on``, M06 missing Alembic gives the installation hint, M07 ``env.py`` is never run, M08 ``require_current``.
The version table is written by the test itself, except in the ``depends_on`` test, which runs a real
``alembic upgrade`` through a script directory of its own (review H-1 of #14); ``status`` never runs ``env.py``.
"""

# The SQL is built from fixed table names of this module, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator, Mapping
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError

from tabrion import Database, Profile, migrations
from tabrion.errors import ConfigurationError, SchemaNotCurrent
from tabrion.migrations import SchemaStatus, require_current, status

ENV_PY = """\
from pathlib import Path
Path(__file__).with_name("env_was_run").write_text("yes")
raise RuntimeError("env.py must not run")
"""

REVISION = """\
revision = {revision!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
"""


def scripts(root: Path, revisions: Mapping[str, str | tuple[str, ...] | None]) -> Path:
    """A script directory with ``revisions`` (revision -> down revision) and an ``env.py`` that must not run."""
    location = root / "migrations"
    (location / "versions").mkdir(parents=True)
    (location / "env.py").write_text(ENV_PY)
    for revision, down in revisions.items():
        (location / "versions" / f"{revision}_step.py").write_text(REVISION.format(revision=revision, down=down))
    return location


REAL_ENV_PY = """\
from alembic import context
from sqlalchemy import create_engine

engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
"""

FULL_REVISION = """\
{extra}
revision = {revision!r}
down_revision = {down!r}
branch_labels = {labels!r}
depends_on = {depends!r}


def upgrade():
    pass


def downgrade():
    pass
"""


def revision_file(
    location: Path,
    revision: str,
    down: str | tuple[str, ...] | None,
    *,
    labels: tuple[str, ...] | None = None,
    depends: str | tuple[str, ...] | None = None,
    extra: str = "",
) -> None:
    """One more revision file in ``location/versions``, with branch labels, ``depends_on`` or extra code."""
    text = FULL_REVISION.format(revision=revision, down=down, labels=labels, depends=depends, extra=extra)
    (location / "versions" / f"{revision}_step.py").write_text(text)


def stamp(database: Database, *revisions: str, table: str = "alembic_version") -> None:
    database.execute(f"CREATE TABLE IF NOT EXISTS {table} (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    for revision in revisions:
        database.execute(f"INSERT INTO {table} (version_num) VALUES (:r)", {"r": revision})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{tmp_path / 'app.db'}")
    try:
        yield database
    finally:
        database.close()


CHAIN = {"a1": None, "a2": "a1", "a3": "a2"}


def test_m01_fresh_database(db: Database, tmp_path: Path) -> None:
    result = status(db, script_location=scripts(tmp_path, CHAIN))
    assert result == SchemaStatus(current=(), heads=("a3",), pending=("a1", "a2", "a3"), is_current=False, unknown=())
    assert db.fetch_scalar("SELECT count(*) FROM sqlite_master") == 0  # no version table was created


def test_m01_empty_script_directory_and_fresh_database_is_current(db: Database, tmp_path: Path) -> None:
    result = status(db, script_location=scripts(tmp_path, {}))
    assert result.is_current and result.heads == () and result.pending == ()


def test_m02_current(db: Database, tmp_path: Path) -> None:
    stamp(db, "a3")
    result = status(db, script_location=str(scripts(tmp_path, CHAIN)))
    assert result == SchemaStatus(current=("a3",), heads=("a3",), pending=(), is_current=True, unknown=())


def test_m03_pending_in_order(db: Database, tmp_path: Path) -> None:
    stamp(db, "a1")
    result = status(db, script_location=scripts(tmp_path, CHAIN))
    assert result.pending == ("a2", "a3") and not result.is_current and result.unknown == ()


def test_m04_unknown_newer_revision(db: Database, tmp_path: Path) -> None:
    stamp(db, "zz9")
    result = status(db, script_location=scripts(tmp_path, CHAIN))
    assert result.unknown == ("zz9",) and result.current == ("zz9",)
    assert result.pending == () and not result.is_current


def test_m05_several_heads(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, {"a1": None, "b1": "a1", "c1": "a1"})
    stamp(db, "b1")
    result = status(db, script_location=location)
    assert result.heads == ("b1", "c1") and result.pending == ("c1",) and not result.is_current
    db.execute("INSERT INTO alembic_version (version_num) VALUES ('c1')")
    assert status(db, script_location=location).is_current


def test_m05_merge_revision(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, {"a1": None, "b1": "a1", "c1": "a1", "m1": ("b1", "c1")})
    stamp(db, "b1")
    result = status(db, script_location=location)
    assert result.heads == ("m1",) and result.pending == ("c1", "m1")


def test_m06_missing_alembic_names_the_extra() -> None:
    code = (
        "import sys\n"
        "sys.modules['alembic'] = None\n"
        "try:\n"
        "    import tabrion.migrations\n"
        "except ImportError as exc:\n"
        "    assert 'pip install \"tabrion[migrations]\"' in str(exc), exc\n"
        "    print('hint ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "hint ok" in result.stdout


@pytest.mark.security
def test_m07_env_py_is_never_run(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    Database.from_url(f"sqlite:///{path}").close()
    location = scripts(tmp_path, CHAIN)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with Database.from_url(f"sqlite:///{path}", profile=Profile.readonly()) as database:
        result = status(database, script_location=location)
        assert result.pending == ("a1", "a2", "a3")
        with pytest.raises(SchemaNotCurrent):
            require_current(database, script_location=location)
    assert not (location / "env_was_run").exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # read only: the file is unchanged


def test_m08_require_current(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, CHAIN)
    stamp(db, "a1")
    with pytest.raises(SchemaNotCurrent) as info:
        require_current(db, script_location=location)
    assert info.value.code == "migrations.not_current"
    assert dict(info.value.details) == {"current": "a1", "head": "a3"}
    assert info.value.__cause__ is None and info.value.__context__ is None
    db.execute("UPDATE alembic_version SET version_num = 'a3'")
    assert require_current(db, script_location=location).is_current


def test_m08_fresh_database_names_no_current(db: Database, tmp_path: Path) -> None:
    with pytest.raises(SchemaNotCurrent) as info:
        require_current(db, script_location=scripts(tmp_path, CHAIN))
    assert dict(info.value.details) == {"current": "", "head": "a3"}


def test_custom_version_table(db: Database, tmp_path: Path) -> None:
    stamp(db, "a2", table="app_versions")
    location = scripts(tmp_path, CHAIN)
    assert status(db, script_location=location, version_table="app_versions").pending == ("a3",)
    assert status(db, script_location=location).current == ()


@pytest.mark.security
def test_bad_script_location_names_no_path(db: Database, tmp_path: Path) -> None:
    missing = tmp_path / "SECRET-4711"
    with pytest.raises(ConfigurationError) as info:
        status(db, script_location=missing)
    assert info.value.code == "migrations.script_location"
    assert "SECRET-4711" not in str(info.value) and "SECRET-4711" not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_argument_types(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, CHAIN)
    with pytest.raises(TypeError):
        status(object(), script_location=location)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        status(db, script_location=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        status(db, script_location=location, version_table="")
    with pytest.raises(TypeError):
        status(db, script_location=location, version_table_schema=3)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        require_current(db)  # script_location is required


def test_m05_sorted_heads_and_current(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, {"r0": None, "zb": "r0", "ab": "r0"})
    stamp(db, "zb", "ab")
    result = status(db, script_location=location)
    assert result == SchemaStatus(current=("ab", "zb"), heads=("ab", "zb"), pending=(), is_current=True, unknown=())


UNEVEN = {"a": None, "b1": "a", "b2": "b1", "c1": "a", "m": ("b2", "c1"), "n": "m"}


@pytest.mark.parametrize(
    ("stamped", "expected"),
    [
        ((), ("a", "c1", "b1", "b2", "m", "n")),
        (("b1",), ("c1", "b2", "m", "n")),
        (("b2", "c1"), ("m", "n")),
        (("n",), ()),
    ],
)
def test_m05_uneven_merge(db: Database, tmp_path: Path, stamped: tuple[str, ...], expected: tuple[str, ...]) -> None:
    """A merge of a two-step branch and a one-step branch: every parent comes before ``m`` (review L-4 of #14)."""
    if stamped:
        stamp(db, *stamped)
    result = status(db, script_location=scripts(tmp_path, UNEVEN))
    assert result.heads == ("n",) and result.pending == expected and result.is_current is (expected == ())


def test_m05_depends_on_after_a_real_upgrade(tmp_path: Path) -> None:
    """``y2`` depends_on ``x2``: after ``alembic upgrade heads`` the version table holds ``y2`` only (review H-1).

    Alembic writes no row for a head that another head depends on, so comparing ``current`` with ``heads`` would
    never call this database current; ``is_current`` therefore goes through ``pending``.
    """
    location = tmp_path / "migrations"
    (location / "versions").mkdir(parents=True)
    (location / "env.py").write_text(REAL_ENV_PY)
    revision_file(location, "x1", None, labels=("xb",))
    revision_file(location, "x2", "x1")
    revision_file(location, "y1", None, labels=("yb",))
    revision_file(location, "y2", "y1", depends="x2")
    path = tmp_path / "app.db"
    config = Config()
    config.set_main_option("script_location", str(location))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "x1")
    command.upgrade(config, "y1")
    with Database.from_url(f"sqlite:///{path}") as database:
        partly = status(database, script_location=location)
        assert partly.current == ("x1", "y1") and partly.heads == ("x2", "y2")
        assert partly.pending == ("x2", "y2") and not partly.is_current
    command.upgrade(config, "heads")
    with Database.from_url(f"sqlite:///{path}") as database:
        assert database.fetch_all("SELECT version_num FROM alembic_version") == [("y2",)]
        done = require_current(database, script_location=location)
    assert done == SchemaStatus(current=("y2",), heads=("x2", "y2"), pending=(), is_current=True, unknown=())


def test_m05_duplicate_heads_are_removed(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    location = scripts(tmp_path, CHAIN)
    monkeypatch.setattr(ScriptDirectory, "get_heads", lambda self: ["a3", "a3"])
    assert status(db, script_location=location).heads == ("a3",)


def broken(location: Path, case: str) -> None:
    if case == "missing down_revision":
        revision_file(location, "a1", "nope")
    elif case == "missing depends_on":
        revision_file(location, "a1", None)
        revision_file(location, "a2", "a1", depends="nope")
    elif case == "branch label as down_revision":
        revision_file(location, "a1", None, labels=("lab",))
        revision_file(location, "a2", "lab")
    elif case == "ImportError":
        revision_file(location, "a1", None, extra="import tabrion_no_such_module")
    elif case == "SyntaxError":
        revision_file(location, "a1", None, extra="def (:")
    else:  # a cycle
        revision_file(location, "a1", "a2")
        revision_file(location, "a2", "a1")


@pytest.mark.filterwarnings("ignore:Revision nope referenced:UserWarning")
@pytest.mark.parametrize(
    "case",
    [
        "missing down_revision",
        "missing depends_on",
        "branch label as down_revision",
        "ImportError",
        "SyntaxError",
        "cycle",
    ],
)
def test_broken_script_directory_is_a_configuration_error(db: Database, tmp_path: Path, case: str) -> None:
    """Every script directory Alembic cannot resolve: one code, no path, no cause chain (review L-1/L-2 of #14)."""
    location = scripts(tmp_path / "SECRET-4711", {})
    broken(location, case)
    with pytest.raises(ConfigurationError) as info:
        status(db, script_location=location)
    assert info.value.code == "migrations.revisions"
    assert "SECRET-4711" not in str(info.value) and "SECRET-4711" not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_revision_and_its_ancestor_in_the_version_table(db: Database, tmp_path: Path) -> None:
    stamp(db, "a1", "a3")  # not something Alembic writes: a3 already covers a1
    with pytest.raises(ConfigurationError) as info:
        status(db, script_location=scripts(tmp_path, CHAIN))
    assert info.value.code == "migrations.revisions"
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_script_location_alembic_refuses_has_no_context(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(location: str) -> ScriptDirectory:
        raise CommandError(f"Path doesn't exist: {location}")

    monkeypatch.setattr(migrations, "ScriptDirectory", refuse)
    location = tmp_path / "SECRET-4711"
    location.mkdir()
    with pytest.raises(ConfigurationError) as info:
        status(db, script_location=location)
    assert info.value.code == "migrations.script_location"
    assert "SECRET-4711" not in str(info.value) and "SECRET-4711" not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None
