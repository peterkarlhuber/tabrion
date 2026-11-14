"""TAB-015 and TAB-016 without a server: validation (TAB-T-P04), argument checks, the result types and the
installation hint without psycopg (contract G-7).

The behavior against a real PostgreSQL is in ``tests/postgres/``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import pytest

from tabrion.errors import ConfigurationError
from tabrion.postgres import (
    RoleInfo,
    RoleReport,
    SessionSettings,
    _host_is_local,
    _milliseconds,
    _validated,
    check_role,
    configure,
    diagnose,
    extension_status,
    grant_runtime_role,
    inspect_role,
    ping,
    role_info,
    session_configurator,
)
from tests.packaging.blocking import BLOCK_SOURCE
from tests.support import run_python

CANARY = "SECRET-4711"


def test_session_settings_defaults() -> None:
    s = SessionSettings()
    assert (s.statement_timeout_ms, s.lock_timeout_ms, s.idle_in_transaction_timeout_ms) == (30_000, 10_000, 300_000)
    assert (s.transaction_timeout_ms, s.application_name, s.search_path, s.timezone) == (None, "tabrion", None, "UTC")


def test_unchanged_sets_nothing() -> None:
    unchanged = SessionSettings.unchanged()
    assert all(getattr(unchanged, f.name) is None for f in dataclasses.fields(unchanged))
    assert _validated(unchanged) == ()


def test_validated_pairs_and_quoting() -> None:
    pairs = _validated(
        SessionSettings(
            statement_timeout_ms=0,
            lock_timeout_ms=2_147_483_647,
            idle_in_transaction_timeout_ms=None,
            transaction_timeout_ms=5,
            application_name="",
            search_path="My_Schema$1",
            timezone="Europe/Vienna",
        )
    )
    assert pairs == (
        ("statement_timeout", "0"),
        ("lock_timeout", "2147483647"),
        ("transaction_timeout", "5"),
        ("application_name", ""),
        ("search_path", '"My_Schema$1"'),
        ("TimeZone", "Europe/Vienna"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("statement_timeout_ms", -1),
        ("lock_timeout_ms", 2_147_483_648),
        ("idle_in_transaction_timeout_ms", True),
        ("transaction_timeout_ms", 1.5),
        ("statement_timeout_ms", "30000"),
        ("search_path", ""),
        ("search_path", "1abc"),
        ("search_path", "a" * 64),
        ("search_path", "a b"),
        ("search_path", 'a"b'),
        ("search_path", f"{CANARY}; RESET ALL"),
        ("search_path", "public,pg_temp"),
        ("search_path", "schéma"),
        ("application_name", "x" * 64),
        ("application_name", "café"),
        ("application_name", "a\nb"),
        ("application_name", 5),
        ("timezone", ""),
        ("timezone", "UTC\x00"),
        ("timezone", 0),
    ],
)
def test_p04_validation(field: str, value: object) -> None:
    changes: dict[str, Any] = {field: value}
    settings = dataclasses.replace(SessionSettings(), **changes)
    for call in (
        lambda: _validated(settings),
        lambda: session_configurator(settings),
        lambda: configure(None, settings),  # type: ignore[arg-type]
    ):
        with pytest.raises(ConfigurationError) as info:
            call()
        assert info.value.code == "postgres.invalid_setting"
        assert dict(info.value.details) == {"setting": field}
        assert CANARY not in str(info.value) + repr(info.value)


@pytest.mark.parametrize("value", ["a", "_", "a" * 63, "A$b_9"])
def test_p04_search_path_accepted(value: str) -> None:
    assert _validated(dataclasses.replace(SessionSettings.unchanged(), search_path=value)) == (
        ("search_path", f'"{value}"'),
    )


def test_p04_application_name_limit() -> None:
    assert _validated(dataclasses.replace(SessionSettings.unchanged(), application_name="~" * 63)) == (
        ("application_name", "~" * 63),
    )


def test_g03_setting_not_allowed() -> None:
    @dataclass(frozen=True, slots=True)
    class WithRole(SessionSettings):
        role: str | None = "postgres"

    with pytest.raises(ConfigurationError) as info:
        _validated(WithRole())
    assert info.value.code == "postgres.setting_not_allowed"
    assert dict(info.value.details) == {"setting": "role"}
    assert _validated(WithRole(role=None)) == _validated(SessionSettings())


def test_settings_type() -> None:
    with pytest.raises(TypeError):
        session_configurator({"statement_timeout_ms": 1})  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, object(), "host=h", 42])
def test_connection_type_is_checked(bad: object) -> None:
    for call in (
        lambda: configure(bad),  # type: ignore[arg-type]
        lambda: session_configurator(SessionSettings())(bad),  # type: ignore[arg-type]
        lambda: diagnose(bad),  # type: ignore[arg-type]
        lambda: extension_status(bad, "vector"),  # type: ignore[arg-type]
        lambda: role_info(bad),  # type: ignore[arg-type]
        lambda: check_role(bad),  # type: ignore[arg-type]
        lambda: inspect_role(bad, "app"),  # type: ignore[arg-type]
        lambda: grant_runtime_role(bad, "app"),  # type: ignore[arg-type]
        lambda: ping(bad),  # type: ignore[arg-type]
    ):
        with pytest.raises(TypeError):
            call()


def test_ping_timeout_is_checked() -> None:
    for bad in ("5", None, True):
        with pytest.raises(TypeError):
            ping(None, timeout=bad)  # type: ignore[arg-type]
    for number in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="timeout"):
            ping(None, timeout=number)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "Vector", "uuid-ossp", "1x", "x;y", 'a"b', "vector "])
def test_extension_name_is_validated(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        extension_status(None, name)  # type: ignore[arg-type]


def test_argument_types() -> None:
    with pytest.raises(TypeError):
        extension_status(None, 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        extension_status(None, "vector", probe_type=5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        inspect_role(None, 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        inspect_role(None, "app", schema=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="owner_scope"):
        role_info(None, owner_scope="tenant")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="owner_scope"):
        check_role(None, owner_scope=["rls"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        check_role(None, allow_table_owner=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("args", "kwargs", "match"),
    [
        (("",), {}, "role"),
        ((5,), {}, "role"),
        (("app",), {"schema": ""}, "schema"),
        (("app",), {"schema": 5}, "schema"),
        (("app",), {"protected_tables": "axentra_schema_migrations"}, "protected_tables"),
        (("app",), {"protected_tables": b"x"}, "protected_tables"),
        (("app",), {"protected_tables": {"x"}}, "protected_tables"),
        (("app",), {"protected_tables": ("x", "")}, "protected_tables"),
        (("app",), {"protected_tables": ("x", 5)}, "protected_tables"),
        (("app",), {"grant_connect": 1}, "grant_connect"),
        (("app",), {"grant_connect": None}, "grant_connect"),
    ],
)
def test_grant_runtime_role_arguments(args: tuple[object, ...], kwargs: dict[str, object], match: str) -> None:
    """TAB-031: the arguments are checked before the connection is touched (here: no connection at all)."""
    with pytest.raises(TypeError, match=match):
        grant_runtime_role(None, *args, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("info", "restricted"),
    [
        (RoleInfo("app", False, False, False), True),
        (RoleInfo("app", True, False, False), False),
        (RoleInfo("app", False, True, False), False),
        (RoleInfo("app", False, False, True), False),
    ],
)
def test_role_info_restricted(info: RoleInfo, restricted: bool) -> None:
    assert info.restricted is restricted


def _report(**changes: object) -> RoleReport:
    base = RoleReport(
        role="app",
        owner="owner",
        schema="s",
        database="db",
        tables=2,
        connect=True,
        superuser=False,
        bypass_rls=False,
        owned_tables=(),
        can_create=False,
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def test_role_report_as_dict() -> None:
    report = _report(owned_tables=("a", "b"))
    d = report.as_dict()
    assert d == {
        "role": "app",
        "owner": "owner",
        "schema": "s",
        "database": "db",
        "tables": 2,
        "superuser": False,
        "bypass_rls": False,
        "owned_tables": ["a", "b"],
        "connect": True,
        "can_create": False,
        "restricted": False,
    }
    d["owned_tables"].append("c")  # type: ignore[attr-defined]
    assert report.owned_tables == ("a", "b")


@pytest.mark.parametrize(
    ("changes", "restricted"),
    [({}, True), ({"superuser": True}, False), ({"bypass_rls": True}, False), ({"owned_tables": ("t",)}, False)],
)
def test_role_report_restricted(changes: dict[str, object], restricted: bool) -> None:
    assert _report(**changes).restricted is restricted
    assert _report(**changes).as_dict()["restricted"] is restricted


@pytest.mark.parametrize(
    ("text", "ms"),
    [("0", 0), ("500ms", 500), ("30s", 30_000), ("5min", 300_000), ("1h", 3_600_000), ("2d", 172_800_000)],
)
def test_milliseconds(text: str, ms: int) -> None:
    assert _milliseconds(text) == ms


def test_milliseconds_rejects_other_text() -> None:
    with pytest.raises(ValueError, match="time setting"):
        _milliseconds("soon")


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("", True),
        ("/var/run/postgresql", True),
        ("@abstract", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("127.8.9.10", True),
        ("::1", True),
        ("fe80::1%eth0", False),
        ("10.0.0.5", False),
        ("db.example.com", False),
        ("localhost.example.com", False),
    ],
)
def test_host_is_local(host: str, local: bool) -> None:
    assert _host_is_local(host) is local


def test_postgres_functions_give_the_installation_hint_without_psycopg() -> None:
    """G-7: ``import tabrion.postgres`` works without psycopg (SessionSettings for layer B); a call raises
    ``ImportError`` with the hint, validation errors still come first where they need no driver."""
    code = BLOCK_SOURCE + (
        "import tabrion.postgres as p\n"
        "s = p.SessionSettings(statement_timeout_ms=1)\n"
        "assert p.SessionSettings.unchanged().timezone is None\n"
        "calls = [lambda: p.configure(None, s), lambda: p.session_configurator(s), lambda: p.ping(None),\n"
        "         lambda: p.diagnose(None), lambda: p.extension_status(None, 'vector'), lambda: p.role_info(None),\n"
        "         lambda: p.check_role(None), lambda: p.inspect_role(None, 'app'), lambda: p.SessionLock('dbname=x'),\n"
        "         lambda: p.grant_runtime_role(None, 'app'),\n"
        "         lambda: p.try_advisory_lock(None, 1), lambda: p.wait_advisory_lock(None, 1),\n"
        "         lambda: p.advisory_unlock(None, 1)]\n"
        "assert p.lock_key('a') == p.lock_key('a')  # pure: no driver needed\n"
        "for call in calls:\n"
        "    try:\n"
        "        call()\n"
        "    except ImportError as exc:\n"
        "        assert 'pip install \"tabrion[postgres] @ git+https://' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('no ImportError without psycopg')\n"
        "print('hint ok')\n"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    assert "hint ok" in result.stdout
