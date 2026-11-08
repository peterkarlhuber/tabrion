"""TAB-020 without a server: ``tabrion.testing.configure``, the URL fixture, the SQLite fixtures, plugin isolation.

TAB-T-X01 configuration and the consumers' variable names, X02 validation, X03 ``tabrion_postgres_url`` (skip,
require, value, never the URL in a message), X08 the SQLite fixtures, X09 the plugin is explicit (no ``pytest11``
entry point; its fixtures exist only with ``pytest_plugins``). Inner pytest runs use ``pytester`` in a subprocess.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
import tomllib

import pytest

from tabrion.testing import _config, configure

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = 'pytest_plugins = ["tabrion.testing"]\n'


@pytest.fixture
def restore_config() -> Iterator[None]:
    saved = _config.current
    yield
    _config.current = saved


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No PostgreSQL variables of any prefix used here (inner runs inherit the environment)."""
    for prefix in ("TABRION", "NODESTRO", "ORKEXA", "AXENTRA"):
        for suffix in ("TEST_POSTGRES", "REQUIRE_POSTGRES", "TEST_RUNTIME_ROLE", "REQUIRE_EXTENSIONS"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)
    monkeypatch.delenv("AXENTRA_TEST_DATABASE_URL", raising=False)
    return monkeypatch


# --- TAB-T-X01: configure ---------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_config")
def test_x01_defaults_and_prefixes() -> None:
    configure()
    settings = _config.current
    assert settings == _config.DEFAULT
    assert (settings.postgres_env, settings.require_env) == (("TABRION_TEST_POSTGRES",), ("TABRION_REQUIRE_POSTGRES",))
    assert (settings.runtime_role_env, settings.require_extensions_env, settings.schema_prefix) == (
        ("TABRION_TEST_RUNTIME_ROLE",),
        ("TABRION_REQUIRE_EXTENSIONS",),
        "t_",
    )
    configure(env_prefix="NODESTRO")
    assert _config.current.postgres_env == ("NODESTRO_TEST_POSTGRES",)
    assert _config.current.require_env == ("NODESTRO_REQUIRE_POSTGRES",)
    assert _config.current.runtime_role_env == ("NODESTRO_TEST_RUNTIME_ROLE",)
    configure(env_prefix="ORKEXA")
    assert _config.current.require_extensions_env == ("ORKEXA_REQUIRE_EXTENSIONS",)
    # Axentra keeps its own names; every call replaces the whole configuration.
    configure(postgres_env=("AXENTRA_TEST_DATABASE_URL",), require_env=("AXENTRA_REQUIRE_POSTGRES",))
    assert _config.current.postgres_env == ("AXENTRA_TEST_DATABASE_URL",)
    assert _config.current.runtime_role_env == ("TABRION_TEST_RUNTIME_ROLE",)
    configure(schema_prefix="ax_t_")
    assert _config.current.schema_prefix == "ax_t_"
    assert _config.current.postgres_env == ("TABRION_TEST_POSTGRES",)


@pytest.mark.usefixtures("restore_config")
def test_x01_variables_first_flag_and_extension_list(clean_env: pytest.MonkeyPatch) -> None:
    configure(postgres_env=("A_URL", "B_URL"), require_env=("A_REQ", "B_REQ"), require_extensions_env=("A_X", "B_X"))
    settings = _config.current
    assert settings.first(settings.postgres_env) is None
    clean_env.setenv("A_URL", "")
    clean_env.setenv("B_URL", "dbname=b")
    assert settings.first(settings.postgres_env) == ("B_URL", "dbname=b")
    clean_env.setenv("A_URL", "dbname=a")
    assert settings.first(settings.postgres_env) == ("A_URL", "dbname=a")
    clean_env.setenv("A_REQ", "0")
    clean_env.setenv("B_REQ", "")
    assert not settings.flag(settings.require_env)  # empty and "0" are off
    for value in ("1", "true", "yes"):  # anything else is on: a typo never skips silently (review Info)
        clean_env.setenv("B_REQ", value)
        assert settings.flag(settings.require_env)
    clean_env.setenv("A_X", "vector, pg_trgm")
    clean_env.setenv("B_X", "citext\tvector")
    assert settings.required_extensions() == {"vector", "pg_trgm", "citext"}


# --- TAB-T-X02: validation --------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_config")
@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"env_prefix": ""}, ValueError),
        ({"env_prefix": "MY-APP"}, ValueError),
        ({"env_prefix": 5}, TypeError),
        ({"postgres_env": "TABRION_TEST_POSTGRES"}, TypeError),
        ({"postgres_env": ()}, ValueError),
        ({"postgres_env": ("OK", "NOT OK")}, ValueError),
        ({"require_env": (5,)}, TypeError),
        ({"runtime_role_env": b"X"}, TypeError),
        ({"require_extensions_env": ("1X",)}, ValueError),
        ({"schema_prefix": "T_"}, ValueError),
        ({"schema_prefix": ""}, ValueError),
        ({"schema_prefix": "t-"}, ValueError),
        ({"schema_prefix": "t_" + "x" * 23}, ValueError),
        ({"schema_prefix": None}, TypeError),
    ],
)
def test_x02_validation(kwargs: dict[str, object], error: type[Exception]) -> None:
    before = _config.current
    with pytest.raises(error):
        configure(**kwargs)  # type: ignore[arg-type]
    assert _config.current is before  # a refused call changes nothing


# --- TAB-T-X03: tabrion_postgres_url --------------------------------------------------------------------------------


URL_TEST = """
def test_url(tabrion_postgres_url):
    assert tabrion_postgres_url == "dbname=x password=secret-value"
"""


def test_x03_url_skips_without_the_variable(pytester: pytest.Pytester, clean_env: pytest.MonkeyPatch) -> None:
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(URL_TEST)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*PostgreSQL tests need one of TABRION_TEST_POSTGRES*"])


def test_x03_url_fails_when_required(pytester: pytest.Pytester, clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("TABRION_REQUIRE_POSTGRES", "1")
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(URL_TEST)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        ["*TABRION_REQUIRE_POSTGRES requires PostgreSQL but none of TABRION_TEST_POSTGRES is set*"]
    )


def test_x03_url_from_the_consumers_names(pytester: pytest.Pytester, clean_env: pytest.MonkeyPatch) -> None:
    """The value comes back unchanged; pytest's report of a failing test shows it with the password masked."""
    pytest.importorskip("psycopg")
    clean_env.setenv("AXENTRA_TEST_DATABASE_URL", "dbname=x password=secret-value")
    pytester.makeconftest(
        PLUGIN + "from tabrion.testing import configure\n"
        'configure(postgres_env=("AXENTRA_TEST_DATABASE_URL",), require_env=("AXENTRA_REQUIRE_POSTGRES",))\n'
    )
    pytester.makepyfile(URL_TEST + "\n\ndef test_other(tabrion_postgres_url):\n    assert False\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    assert "secret-value" not in result.stdout.str()
    result.stdout.fnmatch_lines(["*tabrion_postgres_url = 'dbname=x password=???'*"])


def test_x03_url_without_psycopg(pytester: pytest.Pytester, clean_env: pytest.MonkeyPatch) -> None:
    """A server URL but no driver: skipped with the installation hint (an error when required)."""
    clean_env.setenv("TABRION_TEST_POSTGRES", "dbname=x")
    pytester.makeconftest("import sys\nsys.modules['psycopg'] = None  # blocks the import\n" + PLUGIN)
    pytester.makepyfile("def test_url(tabrion_postgres_url):\n    pass\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*need psycopg: pip install*tabrion?postgres?*"])
    clean_env.setenv("TABRION_REQUIRE_POSTGRES", "1")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(errors=1)


# --- TAB-T-X08: SQLite fixtures --------------------------------------------------------------------------------------


def test_x08_sqlite_path(tabrion_sqlite_path: Path, tmp_path: Path) -> None:
    assert tabrion_sqlite_path.parent == tmp_path
    assert not tabrion_sqlite_path.exists()


def test_x08_sqlite_connection(pytester: pytest.Pytester) -> None:
    """The connection follows the default profile, the new file is 0600, and it is closed after the test."""
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(
        """
        import os
        import sqlite3
        import stat

        import pytest

        seen = []


        def test_profile(tabrion_sqlite, tabrion_sqlite_path):
            seen.append(tabrion_sqlite)
            assert tabrion_sqlite.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert tabrion_sqlite.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert tabrion_sqlite.execute("PRAGMA trusted_schema").fetchone() == (0,)
            if os.name == "posix":
                assert stat.S_IMODE(tabrion_sqlite_path.stat().st_mode) == 0o600


        def test_closed_afterwards():
            with pytest.raises(sqlite3.ProgrammingError):
                seen[0].execute("SELECT 1")
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly").assert_outcomes(passed=2)


# --- TAB-T-X09: the plugin is explicit -------------------------------------------------------------------------------


def test_x09_no_pytest11_entry_point() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "pytest11" not in project.get("entry-points", {})
    installed = [ep for ep in metadata.entry_points(group="pytest11") if ep.value.startswith("tabrion")]
    assert installed == []


def test_x09_fixtures_only_with_pytest_plugins(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_uses(tabrion_sqlite_path):\n    pass\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture 'tabrion_sqlite_path' not found*"])
    pytester.makeconftest(PLUGIN)
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
