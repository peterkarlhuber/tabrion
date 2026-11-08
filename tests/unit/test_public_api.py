"""TAB-002 (API freeze G-T1): the public names of every module match the contract exactly.

The expected lists are the names of ``docs/api/contract.md`` section 0 (doc 04 of the plan). A change here is an API
change and needs the contract updated in the same pull request.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import importlib
import inspect
import sqlite3
from types import ModuleType
from typing import Any

import pytest

EXPECTED: dict[str, set[str]] = {
    # Result and Column are 0.2 names: staged in docs/api/surface.json, exported from 0.2 on (G-T1 condition C-6).
    "tabrion": {
        "Database",
        "Health",
        "Profile",
        "TLSMode",
        "TabrionError",
        "Transaction",
        "__version__",
        "safe_url",
    },
    "tabrion.errors": {
        "Kind",
        "Classification",
        "classify",
        "safe_message",
        "DbErrorRedactionFilter",
        "TabrionError",
        "UsageError",
        "ConfigurationError",
        "DatabaseError",
        "ConnectionFailed",
        "StatementTimeout",
        "Canceled",
        "TransientError",
        "IntegrityViolation",
        "PermissionDenied",
        "ReadOnlyViolation",
        "StatementError",
        "StorageCorrupt",
        "UnknownDatabaseError",
        "MultipleRowsError",
        "MigrationError",
        "SchemaTooNew",
        "MigrationMissing",
        "BackupRequired",
        "BackupFailed",
        "MigrationFailed",
        "SchemaNotCurrent",
        "RoleMissing",
        "RoleUnsafe",
        "UnsafePath",
    },
    "tabrion.sql": {"split_statements"},
    "tabrion.config": {"safe_url", "url_from_env"},
    "tabrion.sqlite": {
        "SQLiteProfile",
        "JournalMode",
        "Synchronous",
        "connect",
        "apply_profile",
        "check_path",
        "UnsafePath",
        "create_private_file",
        "loading_extensions",
        "write_transaction",
        "read_transaction",
        "Migration",
        "migrate",
        "MigrationResult",
        "backup",
        "BackupResult",
        "integrity_check",
        "quick_check",
        "IntegrityReport",
        "foreign_key_check",
        "ForeignKeyViolation",
        "diagnose",
        "SQLiteDiagnosis",
    },
    "tabrion.postgres": {
        "SessionSettings",
        "configure",
        "session_configurator",
        "ping",
        "PingResult",
        "diagnose",
        "PostgresDiagnosis",
        "extension_status",
        "ExtensionStatus",
        "RoleInfo",
        "RoleReport",
        "role_info",
        "check_role",
        "inspect_role",
        "grant_runtime_role",
        "SessionLock",
        "lock_key",
        "try_advisory_lock",
        "wait_advisory_lock",
        "advisory_unlock",
    },
    "tabrion.postgres.roles": {
        "RoleInfo",
        "RoleReport",
        "role_info",
        "check_role",
        "inspect_role",
        "grant_runtime_role",
    },
    "tabrion.postgres.locks": {
        "SessionLock",
        "lock_key",
        "try_advisory_lock",
        "wait_advisory_lock",
        "advisory_unlock",
    },
    "tabrion.check": {"check", "Finding", "Severity"},
    "tabrion.migrations": {"status", "require_current", "SchemaStatus"},
    "tabrion.testing": {
        "configure",
        "profiles",
        "tabrion_sqlite_path",
        "tabrion_sqlite",
        "tabrion_postgres_url",
        "tabrion_postgres_schema",
        "tabrion_postgres_database",
        "tabrion_postgres_roles",
        "tabrion_role_setup",
        "postgres_server_version",
        "truncate_tables",
        "require_extension",
        "RoleSetup",
        "TestRoles",
        "PostgresSchema",
        "PostgresDatabaseHandle",
    },
    "tabrion.testing.profiles": {
        "SQLiteConnect",
        "ProfileCase",
        "PortabilityCase",
        "sqlite_profile_cases",
        "portability_cases",
    },
}


@pytest.mark.parametrize("module_name", sorted(EXPECTED))
def test_all_matches_the_contract(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert set(module.__all__) == EXPECTED[module_name]
    assert len(module.__all__) == len(set(module.__all__)), "duplicate names in __all__"
    for name in module.__all__:
        assert getattr(module, name) is not None, name


def test_layer_b_names_resolve_lazily() -> None:
    import tabrion
    from tabrion._sa.database import Database, Health, Transaction
    from tabrion._sa.profile import Profile, TLSMode
    from tabrion.config import safe_url
    from tabrion.errors import TabrionError

    assert tabrion.Database is Database
    assert tabrion.Transaction is Transaction
    assert tabrion.Health is Health
    assert tabrion.Profile is Profile
    assert tabrion.TLSMode is TLSMode
    assert tabrion.safe_url is safe_url
    assert tabrion.TabrionError is TabrionError
    assert set(tabrion.__all__) <= set(dir(tabrion))
    with pytest.raises(AttributeError):
        tabrion.does_not_exist  # noqa: B018


@pytest.mark.parametrize("name", ["Result", "Column"])
def test_0_2_names_are_not_exported_yet(name: str) -> None:
    """G-T1 condition C-6: the 0.1.0 surface exports no 0.2 name (``scripts/api_surface.py`` checks the rest)."""
    import tabrion

    assert name not in tabrion.__all__
    with pytest.raises(AttributeError):
        getattr(tabrion, name)


@pytest.mark.parametrize(
    ("cls", "methods"),
    [
        ("Database", ["query", "inspector", "on_begin", "run_transaction"]),
        ("Transaction", ["query", "stream", "cancel", "savepoint"]),
    ],
)
def test_0_2_methods_are_staged_not_mixed_in(cls: str, methods: list[str]) -> None:
    from tabrion._sa import _v02, database

    public = getattr(database, cls)
    staged = getattr(_v02, f"_{cls}V02")
    for method in methods:
        assert not hasattr(public, method), f"{cls}.{method} is a 0.2 name"
        assert callable(getattr(staged, method)), f"{cls}.{method} is not staged"
    assert staged not in public.__mro__


def test_health_has_deep() -> None:
    """G-T1 condition C-3 (G-9): ``deep=True`` adds the SQLite ``quick_check``; keyword-only, default off."""
    from tabrion._sa.database import Database

    param = inspect.signature(Database.health).parameters["deep"]
    assert (param.kind, param.default) == (inspect.Parameter.KEYWORD_ONLY, False)


def _public_callables(module: ModuleType) -> list[tuple[str, Callable[..., Any]]]:
    found: list[tuple[str, Callable[..., Any]]] = []
    for name in module.__all__:
        obj = getattr(module, name)
        if inspect.isfunction(obj) and obj.__module__.startswith("tabrion"):
            found.append((name, obj))
    return found


@pytest.mark.parametrize("module_name", sorted(EXPECTED))
def test_every_public_function_is_fully_annotated(module_name: str) -> None:
    module = importlib.import_module(module_name)
    for name, fn in _public_callables(module):
        sig = inspect.signature(fn)
        assert sig.return_annotation is not inspect.Signature.empty, f"{module_name}.{name} has no return type"
        for param in sig.parameters.values():
            assert param.annotation is not inspect.Parameter.empty, f"{module_name}.{name}({param.name}) untyped"


def _dummy_args(fn: Callable[..., Any]) -> tuple[list[Any], dict[str, Any]]:
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for param in inspect.signature(fn).parameters.values():
        if param.default is not inspect.Parameter.empty or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.kind is param.KEYWORD_ONLY:
            kwargs[param.name] = None
        else:
            args.append(None)
    return args, kwargs


# Implemented modules leave this list (TAB-010 errors, TAB-011 sql, TAB-012 config, TAB-013/TAB-014 sqlite, TAB-015,
# TAB-016, TAB-030 and TAB-031 postgres); the rest are M1 stubs.
STUB_MODULES = [
    "tabrion.check",
]


# Functions of a partly implemented module that left the stub check; the module's other functions must still raise
# NotImplementedError. Empty since TAB-031 completed tabrion.postgres.
IMPLEMENTED: set[str] = set()


@pytest.mark.parametrize("module_name", [*STUB_MODULES, "tabrion.testing.profiles"])
def test_stubs_raise_not_implemented(module_name: str) -> None:
    """M1 delivers signatures only: every function raises NotImplementedError (implementation in M2)."""
    module = importlib.import_module(module_name)
    functions = _public_callables(module)
    assert functions
    for _name, fn in functions:
        if f"{module_name}.{_name}" in IMPLEMENTED:
            continue
        args, kwargs = _dummy_args(fn)
        with pytest.raises(NotImplementedError):
            result = fn(*args, **kwargs)
            # Context managers raise on entry.
            if hasattr(result, "__enter__"):
                result.__enter__()


def test_return_dataclasses_are_frozen_and_slotted() -> None:
    """Contract convention: every result dataclass is ``@dataclass(frozen=True, slots=True)``."""
    import tabrion
    from tabrion import check, errors, migrations, postgres, sqlite
    from tabrion.testing import postgres as testing_postgres
    from tabrion.testing import profiles

    modules: list[ModuleType] = [errors, sqlite, postgres, check, migrations, testing_postgres, profiles]
    classes = [getattr(m, n) for m in modules for n in m.__all__ if dataclasses.is_dataclass(getattr(m, n))]
    from tabrion._sa.result import Column, Result

    classes += [tabrion.Profile, tabrion.Health, Result, Column]
    assert len(classes) >= 20
    for cls in classes:
        params = cls.__dataclass_params__
        assert params.frozen, cls
        assert "__slots__" in cls.__dict__, cls


def test_default_profiles_are_constructible() -> None:
    """Defaults used as argument defaults must not run code that is not implemented yet."""
    import tabrion
    from tabrion.postgres import SessionSettings
    from tabrion.sqlite import JournalMode, SQLiteProfile, Synchronous

    profile = SQLiteProfile()
    assert (profile.journal_mode, profile.synchronous, profile.foreign_keys) == (
        JournalMode.WAL,
        Synchronous.FULL,
        True,
    )
    assert (profile.busy_timeout, profile.secure_delete, profile.trusted_schema, profile.file_mode) == (
        30.0,
        True,
        False,
        0o600,
    )
    settings = SessionSettings()
    assert (settings.statement_timeout_ms, settings.lock_timeout_ms, settings.idle_in_transaction_timeout_ms) == (
        30_000,
        10_000,
        300_000,
    )
    assert (settings.transaction_timeout_ms, settings.application_name, settings.timezone) == (None, "tabrion", "UTC")
    layer_b = tabrion.Profile()
    assert (layer_b.pool_size, layer_b.max_overflow, layer_b.pool_pre_ping, layer_b.max_rows) == (5, 0, True, 10_000)
    assert layer_b.tls is tabrion.TLSMode.AUTO


def test_kind_values() -> None:
    from tabrion.errors import Kind

    assert [k.value for k in Kind] == [
        "connection",
        "timeout",
        "canceled",
        "locked",
        "deadlock",
        "serialization",
        "unique",
        "foreign_key",
        "not_null",
        "check",
        "exclusion",
        "data",
        "undefined_object",
        "syntax",
        "permission",
        "read_only",
        "corrupt",
        "disk_full",
        "resource",
        "unknown",
    ]


def test_error_hierarchy() -> None:
    from tabrion import errors as e

    assert issubclass(e.TabrionError, Exception)
    for cls in (e.UsageError, e.ConfigurationError, e.DatabaseError, e.MultipleRowsError, e.MigrationError):
        assert cls.__bases__ == (e.TabrionError,)
    for cls in (
        e.ConnectionFailed,
        e.StatementTimeout,
        e.Canceled,
        e.TransientError,
        e.IntegrityViolation,
        e.PermissionDenied,
        e.ReadOnlyViolation,
        e.StatementError,
        e.StorageCorrupt,
        e.UnknownDatabaseError,
    ):
        assert cls.__bases__ == (e.DatabaseError,)
    for cls in (e.SchemaTooNew, e.MigrationMissing, e.BackupRequired, e.BackupFailed, e.MigrationFailed):
        assert cls.__bases__ == (e.MigrationError,)
    for other in (e.SchemaNotCurrent, e.RoleMissing, e.RoleUnsafe, e.UnsafePath):
        assert other.__bases__ == (e.TabrionError,)


def test_no_model_specific_names() -> None:
    """TAB-ADR-0010 (model neutrality): no public name about tenants, users or products."""
    for module_name, names in EXPECTED.items():
        for name in names:
            lowered = name.lower()
            assert not any(word in lowered for word in ("tenant", "user", "product")), f"{module_name}.{name}"


def test_sqlite_connection_type_is_stdlib() -> None:
    from tabrion import sqlite

    assert inspect.signature(sqlite.connect).return_annotation in ("sqlite3.Connection", sqlite3.Connection)
