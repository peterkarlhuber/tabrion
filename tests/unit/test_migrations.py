"""``tabrion.migrations`` on SQLite (TAB-019): TAB-T-M01..M08 (contract section 5.5, implementation note I-57).

M01 fresh database, M02 current, M03 pending, M04 unknown newer revision, M05 several heads, M06 missing Alembic
gives the installation hint, M07 ``env.py`` is never run, M08 ``require_current``. The version table is written by
the test itself, so no test runs ``env.py`` or ``alembic upgrade``.
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

from tabrion import Database, Profile
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
    assert info.value.__cause__ is None


def test_broken_revision_graph(db: Database, tmp_path: Path) -> None:
    location = scripts(tmp_path, {"a1": "a2", "a2": "a1"})  # a cycle
    with pytest.raises(ConfigurationError) as info:
        status(db, script_location=location)
    assert info.value.code == "migrations.revisions"


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
