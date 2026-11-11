"""The two variants of the measurements application under one test suite (RA-B-4, for RA-EVAL).

``examples/measurements`` (package ``measurements``, Tabrion) and ``examples/measurements-baseline`` (package
``measurements_baseline``, plain SQLAlchemy) have the same domain API: ``Measurement``, ``Store``,
``open_database``, ``transfer``, ``open_upstream``, ``create_upstream``, the CLI. They differ in the database handle
(a ``sqlite3.Connection`` and a ``tabrion.Database`` against two ``sqlalchemy.Engine``), so the tests reach the
handle only through a ``Variant``: open and close it, write or read raw SQL white-box, run a function on every
DB-API connection (a trace, a progress handler), read the upstream.

The fixture ``app`` runs every test of ``test_measurements*.py`` against both. Where the baseline does not have a
property the test checks, ``DEVIATIONS`` names the test and the reason: there the baseline is a strict ``xfail``
(an unexpected pass fails the run). The deviations are the probe hits RA-EVAL counts (metric MW-2, planning
document 09 section 7), each with the metric it belongs to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
from pathlib import Path
import sqlite3
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa

if TYPE_CHECKING:
    # The tests reach both packages through importlib; these imports make mypy check them (``mypy_path``).
    import measurements.cli
    import measurements.transfer
    import measurements.upstream  # noqa: F401
    import measurements_baseline.cli
    import measurements_baseline.transfer
    import measurements_baseline.upstream  # noqa: F401

DEVIATIONS: dict[str, str] = {
    "test_files_are_owner_only": (
        "MW-2 (S: file modes): SQLAlchemy creates the database, its WAL files and the backups with the umask "
        "(0644 under 022) and the backup directory 0755; Tabrion makes them 0600 and 0700"
    ),
    "test_capture_runs_without_the_transfer_modules": (
        "edge suitability: the baseline's capture needs SQLAlchemy; Tabrion's capture is standard library only"
    ),
    "test_a_lost_commit_reply_is_reported_as_outcome_unknown": (
        "MW-2 (P: error model): SQLAlchemy cannot say that a lost COMMIT answer leaves the outcome unknown"
    ),
    "test_an_upstream_error_is_classified": (
        "MW-2 (P: error model): SQLAlchemy's errors carry no classification; the transfer cannot tell a lost "
        "COMMIT (outcome unknown) from a refused connection"
    ),
}
"""Test name -> why the baseline fails it (a strict ``xfail`` for the baseline only).

The rule (review M-1 of #28): a documented one-line option of SQLAlchemy is not extra code, so the baseline uses
it (``hide_parameters=True``, ``pool_pre_ping``), and a probe it would pass is no deviation. The two error-model
entries follow from ``Baseline.outcome_unknown`` and ``Baseline.classified_as_connection``: SQLAlchemy has no API
for either, so the adapter can only report what ``DBAPIError`` says."""


@dataclass
class Local:
    """An open local database of either variant."""

    handle: Any
    """``sqlite3.Connection`` (Tabrion) or ``sqlalchemy.Engine`` (baseline)."""
    store: Any
    path: Path
    variant: Variant

    def close(self) -> None:
        self.variant.close(self)

    def write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """White-box SQL in a write transaction of its own, beside the application."""
        with sqlite3.connect(self.path, timeout=10) as raw:
            raw.execute(sql, params)
        raw.close()

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def idle(self) -> bool:
        """No transaction of the application is open: a writer gets ``BEGIN IMMEDIATE`` at once."""
        connection = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            connection.close()

    def integrity_ok(self) -> bool:
        return self.query("PRAGMA integrity_check") == [("ok",)]


@dataclass
class Upstream:
    """An open upstream of either variant: ``handle`` goes to ``transfer``."""

    handle: Any
    variant: Variant

    def rows(self, statement: sa.Select[Any]) -> list[Any]:
        return self.variant.upstream_rows(self.handle, statement)

    def scalar(self, statement: sa.Select[Any]) -> Any:
        rows = self.rows(statement)
        return rows[0][0] if rows else None


class Variant:
    name: str
    package: str
    directory: str

    def module(self, name: str = "") -> ModuleType:
        return importlib.import_module(f"{self.package}.{name}" if name else self.package)

    def __getattr__(self, name: str) -> Any:
        # The domain API, the same names in both packages.
        for module in ("store", "db", "transfer", "upstream", "cli"):
            found = getattr(self.module(module), name, None)
            if found is not None:
                return found
        raise AttributeError(name)

    def open(self, path: Path, *, backup_dir: Path | None = None, target: int | None = None) -> Local:
        options: dict[str, Any] = {"backup_dir": backup_dir or path.parent / "backups"}
        if target is not None:
            options["target"] = target
        handle = self.module("db").open_database(path, **options)
        return Local(handle, self.module("store").Store(handle), path, self)

    # Per variant
    def close(self, local: Local) -> None:
        raise NotImplementedError

    def migrate_without_backup(self, local: Local, target: int) -> None:
        raise NotImplementedError

    def instrument(self, local: Local, setup: Callable[[sqlite3.Connection], None]) -> None:
        """Run ``setup`` on the DB-API connection(s) the application writes through, from now on."""
        raise NotImplementedError

    @contextmanager
    def upstream(self, url: str | sa.URL) -> Iterator[Upstream]:
        raise NotImplementedError
        yield

    def upstream_rows(self, handle: Any, statement: sa.Select[Any]) -> list[Any]:
        raise NotImplementedError

    def classified_as_connection(self, error: BaseException) -> bool:
        raise NotImplementedError

    def outcome_unknown(self, error: BaseException) -> bool:
        raise NotImplementedError


class Tabrion(Variant):
    name, package, directory = "tabrion", "measurements", "measurements"

    @property
    def BackupRequired(self) -> type[Exception]:  # the same name as the baseline's class
        from tabrion.errors import BackupRequired

        return BackupRequired

    @property
    def BackupFailed(self) -> type[Exception]:
        from tabrion.errors import BackupFailed

        return BackupFailed

    def close(self, local: Local) -> None:
        local.handle.close()

    def migrate_without_backup(self, local: Local, target: int) -> None:
        from tabrion import sqlite as tsqlite

        tsqlite.migrate(local.handle, self.module("db").MIGRATIONS, target=target, backup=None)

    def instrument(self, local: Local, setup: Callable[[sqlite3.Connection], None]) -> None:
        setup(local.handle)

    @contextmanager
    def upstream(self, url: str | sa.URL) -> Iterator[Upstream]:
        with self.module("upstream").open_upstream(url) as database:
            yield Upstream(database, self)

    def upstream_rows(self, handle: Any, statement: sa.Select[Any]) -> list[Any]:
        return list(handle.fetch_all(statement))

    def classified_as_connection(self, error: BaseException) -> bool:
        from tabrion.errors import Kind

        classification = getattr(error, "classification", None)
        return classification is not None and classification.kind is Kind.CONNECTION

    def outcome_unknown(self, error: BaseException) -> bool:
        classification = getattr(error, "classification", None)
        return bool(classification is not None and classification.outcome_unknown)


class Baseline(Variant):
    name, package, directory = "baseline", "measurements_baseline", "measurements-baseline"

    def close(self, local: Local) -> None:
        local.handle.dispose()

    def migrate_without_backup(self, local: Local, target: int) -> None:
        self.module("db").migrate(local.handle, target=target, backup=None)

    def instrument(self, local: Local, setup: Callable[[sqlite3.Connection], None]) -> None:
        local.handle.dispose()  # the pool's next connection is a new one, set up by the listener
        sa.event.listen(local.handle, "connect", lambda dbapi_connection, _record: setup(dbapi_connection))

    @contextmanager
    def upstream(self, url: str | sa.URL) -> Iterator[Upstream]:
        engine = self.module("upstream").open_upstream(url)
        try:
            yield Upstream(engine, self)
        finally:
            engine.dispose()

    def upstream_rows(self, handle: Any, statement: sa.Select[Any]) -> list[Any]:
        with handle.connect() as connection:
            return list(connection.execute(statement).fetchall())

    def classified_as_connection(self, error: BaseException) -> bool:
        # What a baseline can tell: SQLAlchemy marks a DB-API error that invalidated the connection.
        return isinstance(error, sa.exc.DBAPIError) and bool(error.connection_invalidated)

    def outcome_unknown(self, error: BaseException) -> bool:
        return False  # SQLAlchemy has no notion of it


VARIANTS: dict[str, Variant] = {variant.name: variant for variant in (Tabrion(), Baseline())}


@pytest.fixture(params=sorted(VARIANTS))
def app(request: pytest.FixtureRequest) -> Variant:
    """The application under test: ``tabrion`` or ``baseline``; a listed deviation is a strict ``xfail``."""
    variant = VARIANTS[request.param]
    reason = DEVIATIONS.get(request.node.originalname)
    if variant.name == "baseline" and reason is not None:
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=reason))
    return variant
