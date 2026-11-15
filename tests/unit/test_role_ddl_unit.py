"""TAB-XW-8 without a server: argument checks, name parsing, the result type and the findings logic.

Contract section 4.9, I-180..I-186. Every argument is checked before the connection is touched, so the calls below
pass ``object()`` as the connection and still get the ``UsageError`` of the argument; a password never reaches a
frame of the traceback. The behavior against a real PostgreSQL is in ``tests/postgres/test_role_ddl.py``.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from tabrion.errors import UsageError
from tabrion.postgres import (
    RoleOptions,
    RolePrivileges,
    alter_role,
    create_role,
    drop_role,
    grant_membership,
    grant_privileges,
    revoke_membership,
    revoke_privileges,
    role_privileges,
)
from tabrion.postgres import _roleddl as roleddl

CANARY = "SECRET-4711"
NOT_A_CONNECTION: Any = object()
PACKAGE = str(Path(roleddl.__file__).resolve().parent.parent)
"""The ``tabrion`` package directory: the frames whose locals must not hold a password."""


def _code(call: Callable[[], object]) -> tuple[str, dict[str, object]]:
    with pytest.raises(UsageError) as caught:
        call()
    return caught.value.code, dict(caught.value.details)


def _privileges(**changes: Any) -> RolePrivileges:
    fields: dict[str, Any] = {
        "role": "app",
        "attributes": ("login", "inherit"),
        "member_of": (),
        "reachable": (),
        "dangerous": (),
        "broad": (),
        "owned_objects": 0,
        "default_privileges": 0,
    }
    fields.update(changes)
    return RolePrivileges(**fields)


BAD_NAMES: list[object] = ["", "a\x00b", "x" * 64, "ä" * 32, "\ud800", 7, None, b"app"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_role_names_checked_first(name: object) -> None:
    calls: list[Callable[[], object]] = [
        lambda: role_privileges(NOT_A_CONNECTION, name),  # type: ignore[arg-type]
        lambda: create_role(NOT_A_CONNECTION, name),  # type: ignore[arg-type]
        lambda: alter_role(NOT_A_CONNECTION, name, password=CANARY),  # type: ignore[arg-type]
        lambda: drop_role(NOT_A_CONNECTION, name),  # type: ignore[arg-type]
        lambda: grant_membership(NOT_A_CONNECTION, name, member="m"),  # type: ignore[arg-type]
        lambda: revoke_membership(NOT_A_CONNECTION, name, member="m"),  # type: ignore[arg-type]
        lambda: grant_privileges(NOT_A_CONNECTION, name, privileges=["USAGE"], on="schema", objects=["s"]),  # type: ignore[arg-type]
        lambda: revoke_privileges(NOT_A_CONNECTION, name, privileges=["USAGE"], on="schema", objects=["s"]),  # type: ignore[arg-type]
    ]
    for call in calls:
        assert _code(call) == ("postgres.invalid_argument", {"parameter": "role"})
    assert _code(lambda: grant_membership(NOT_A_CONNECTION, "r", member=name)) == (  # type: ignore[arg-type]
        "postgres.invalid_argument",
        {"parameter": "member"},
    )


def test_name_of_63_bytes_accepted_and_reserved_prefix_refused() -> None:
    with pytest.raises(TypeError):  # the name passed; the connection is next
        create_role(NOT_A_CONNECTION, "x" * 63)
    reserved = _code(lambda: create_role(NOT_A_CONNECTION, "pg_app"))
    assert reserved == ("postgres.invalid_argument", {"parameter": "role"})
    assert _code(lambda: grant_membership(NOT_A_CONNECTION, "same", member="same")) == (
        "postgres.invalid_argument",
        {"parameter": "member"},
    )


@pytest.mark.parametrize(
    ("options", "parameter"),
    [
        (RoleOptions(login=1), "options.login"),  # type: ignore[arg-type]
        (RoleOptions(inherit=None), "options.inherit"),  # type: ignore[arg-type]
        (RoleOptions(create_db="yes"), "options.create_db"),  # type: ignore[arg-type]
        (RoleOptions(connection_limit=-1), "options.connection_limit"),
        (RoleOptions(connection_limit=True), "options.connection_limit"),
        (RoleOptions(connection_limit=2**31), "options.connection_limit"),
        (RoleOptions(connection_limit=1.5), "options.connection_limit"),  # type: ignore[arg-type]
        (RoleOptions(valid_until=datetime.datetime(2030, 1, 1)), "options.valid_until"),  # naive on purpose
        (RoleOptions(valid_until=datetime.date(2030, 1, 1)), "options.valid_until"),  # type: ignore[arg-type]
        ({"login": True}, "options"),
    ],
)
def test_options_checked(options: Any, parameter: str) -> None:
    assert _code(lambda: create_role(NOT_A_CONNECTION, "app", options=options)) == (
        "postgres.invalid_argument",
        {"parameter": parameter},
    )
    assert _code(lambda: alter_role(NOT_A_CONNECTION, "app", options=options)) == (
        "postgres.invalid_argument",
        {"parameter": parameter},
    )


def test_role_options_have_no_banned_attribute() -> None:
    names = {field.name for field in dataclasses.fields(RoleOptions)}
    assert names == {"login", "inherit", "create_db", "connection_limit", "valid_until"}
    assert RoleOptions() == RoleOptions(login=False, inherit=True, create_db=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        RoleOptions().login = True  # type: ignore[misc]


def test_alter_role_needs_something_to_change() -> None:
    assert _code(lambda: alter_role(NOT_A_CONNECTION, "app")) == ("postgres.invalid_argument", {"parameter": "options"})


def _frame_texts(tb: TracebackType | None) -> list[str]:
    texts: list[str] = []
    while tb is not None:
        if tb.tb_frame.f_code.co_filename.startswith(PACKAGE):  # Tabrion's frames, not the test's own
            texts += [repr(value) for value in tb.tb_frame.f_locals.values()]
            texts += [value for value in tb.tb_frame.f_locals.values() if isinstance(value, str)]
        tb = tb.tb_next
    return texts


@pytest.mark.parametrize("password", ["", "a\x00" + CANARY, "\ud800" + CANARY, 4711, b"SECRET-4711"])
def test_password_checked_and_never_in_frames(password: Any) -> None:
    calls: list[Callable[[], object]] = [
        lambda: create_role(NOT_A_CONNECTION, "app", password=password),
        lambda: alter_role(NOT_A_CONNECTION, "app", password=password),
    ]
    for call in calls:
        with pytest.raises(UsageError) as caught:
            call()
        assert dict(caught.value.details) == {"parameter": "password"}
        texts = [str(caught.value), repr(caught.value), *_frame_texts(caught.value.__traceback__)]
        assert not any(CANARY in text for text in texts)


def test_password_not_in_frames_of_a_later_error() -> None:
    """A valid password and an invalid name: the frames hold neither the password nor its secret's value."""
    calls: list[Callable[[], object]] = [
        lambda: create_role(NOT_A_CONNECTION, "", password=CANARY),
        lambda: alter_role(NOT_A_CONNECTION, "app", options=RoleOptions(login=2), password=CANARY),  # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(UsageError) as caught:
            call()
        assert not any(CANARY in text for text in _frame_texts(caught.value.__traceback__))
    with pytest.raises(TypeError) as wrong:
        create_role(NOT_A_CONNECTION, "app", password=CANARY)
    assert not any(CANARY in text for text in _frame_texts(wrong.value.__traceback__))


def test_secret_repr_and_clear() -> None:
    secret = roleddl._Secret(CANARY)
    assert CANARY not in repr(secret) and secret.encoded() == CANARY.encode()
    secret.clear()
    with pytest.raises(RuntimeError):
        secret.encoded()


@pytest.mark.parametrize(
    ("privileges", "on"),
    [
        (["TRIGGER"], "table"),
        (["ALL"], "table"),
        (["select"], "table"),
        (["EXECUTE"], "table"),
        (["SELECT"], "function"),
        (["CREATE"], "sequence"),
        (["TEMPORARY"], "database"),
        (["MAINTAIN"], "table"),
        ([], "table"),
        ("SELECT", "table"),
        ([1], "table"),
    ],
)
def test_privileges_checked_against_the_object_kind(privileges: Any, on: str) -> None:
    assert _code(lambda: grant_privileges(NOT_A_CONNECTION, "app", privileges=privileges, on=on, objects=["s.t"])) == (  # type: ignore[arg-type]
        "postgres.invalid_argument",
        {"parameter": "privileges"},
    )


def test_trigger_only_revocable() -> None:
    with pytest.raises(TypeError):  # the arguments passed; the connection is next
        revoke_privileges(NOT_A_CONNECTION, "app", privileges=["TRIGGER"], on="table", objects=["s.t"])
    on_schema = _code(
        lambda: revoke_privileges(NOT_A_CONNECTION, "app", privileges=["TRIGGER"], on="schema", objects=["s"])
    )
    assert on_schema == ("postgres.invalid_argument", {"parameter": "privileges"})
    assert _code(lambda: grant_privileges(NOT_A_CONNECTION, "app", privileges=["USAGE"], on="view", objects=["s"])) == (  # type: ignore[arg-type]
        "postgres.invalid_argument",
        {"parameter": "on"},
    )


@pytest.mark.parametrize(
    ("on", "objects"),
    [
        ("table", "s.t"),
        ("table", []),
        ("table", ["t"]),
        ("table", ["a.b.c"]),
        ("table", ["s. t"]),
        ("table", ['s."t']),
        ("table", ['s.""']),
        ("table", [f"s.{'x' * 64}"]),
        ("table", ["s.t\x00"]),
        ("table", [None]),
        ("function", ["s.f"]),
        ("function", ["f(integer)"]),
        ("function", ["s.f(integer); DROP ROLE x"]),
        ("schema", [""]),
        ("schema", ["x" * 64]),
        ("database", ["d\x00"]),
    ],
)
def test_objects_checked(on: str, objects: Any) -> None:
    privilege: Any = {"table": "SELECT", "function": "EXECUTE", "schema": "USAGE", "database": "CONNECT"}[on]
    assert _code(lambda: grant_privileges(NOT_A_CONNECTION, "app", privileges=[privilege], on=on, objects=objects)) == (  # type: ignore[arg-type]
        "postgres.invalid_argument",
        {"parameter": "objects"},
    )


def test_object_names_parsed_as_sql() -> None:
    parsed = roleddl._check_objects(['App."Mixed ""Case"""', "s.T_1$", 'ÄBC."x.y"'], "table")
    assert parsed == (("app", 'Mixed "Case"'), ("s", "t_1$"), ("Äbc", "x.y"))
    functions = roleddl._check_objects(["S.f(integer, numeric(10, 2), text[])", 's."F"()'], "function")
    assert functions == (
        ("s", "f", "S.f(integer, numeric(10, 2), text[])"),
        ("s", "F", 's."F"()'),
    )
    assert roleddl._check_objects(["My Schema"], "schema") == (("My Schema",),)


def test_role_privileges_result() -> None:
    clean = _privileges(broad=("broad:pg_read_all_data",))
    assert clean.safe  # broad is a finding of its own
    unsafe = _privileges(dangerous=("trigger:s.t",))
    assert not unsafe.safe
    assert unsafe.as_dict() == {
        "role": "app",
        "attributes": ["login", "inherit"],
        "member_of": [],
        "reachable": [],
        "dangerous": ["trigger:s.t"],
        "broad": [],
        "owned_objects": 0,
        "default_privileges": 0,
        "safe": False,
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        clean.role = "other"  # type: ignore[misc]


def test_reasons_from_findings() -> None:
    privileges = _privileges(
        dangerous=("bypass_rls:app", "createrole:boss", "schema_create:public", "superuser:root", "trigger:s.t")
    )
    assert roleddl._attribute_reasons(privileges) == ["bypass_rls", "reachable_createrole", "reachable_superuser"]
    assert roleddl._attribute_reasons(privileges, own=False) == [
        "reachable_bypass_rls",
        "reachable_createrole",
        "reachable_superuser",
    ]
    assert roleddl._escalation_reasons(privileges.dangerous) == ["trigger", "schema_create"]
    before = _privileges(dangerous=("schema_create:public",))
    after = _privileges(
        dangerous=("replication:rep", "schema_create:public", "security_definer:s.f(integer)"),
        broad=("broad:pg_monitor",),
    )
    # Only what is new after the change counts; a grant to PUBLIC that was there before does not.
    assert roleddl._new_findings(before, after) == ["reachable_replication", "security_definer", "predefined_role"]
    assert roleddl._new_findings(after, after) == []
