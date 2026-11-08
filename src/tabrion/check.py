"""Read-only configuration and security checks with stable finding codes (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 7; the codes are listed in ``docs/specification.md`` section 9.
Stub for the API freeze G-T1; the implementation follows in TAB-017.

Dispatch (G-T1 condition C-1, fix B; contract section 7, ADR 0012): this module checks SQLite connections itself and
never imports ``tabrion.postgres`` or layer B, not even inside a function. The PostgreSQL checks live in
``tabrion.postgres._check`` and the layer-B checks in ``tabrion._sa._check``; ``check()`` picks the collector by the
module of the target's type and loads it by name from the fixed table ``_COLLECTORS``. The import contracts stay
unchanged, and ``tests/architecture`` checks that this module has no static import of either package and that the
table names only those two collectors.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import importlib
import sqlite3
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import psycopg

    from tabrion._sa.database import Database

__all__ = ["Finding", "Severity", "check"]


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    """Stable, e.g. ``TAB-SQ-001``."""
    severity: Severity
    title: str
    detail: str
    """Never contains secrets."""
    remedy: str


_COLLECTORS: dict[str, str] = {
    "psycopg": "tabrion.postgres._check",
    "tabrion._sa.database": "tabrion._sa._check",
}
"""Module of the target's type (or of a base class) -> the collector module with ``collect(target, *, expect)``.

The keys match the type's module exactly or as a package prefix (``psycopg`` covers ``psycopg.connection``)."""


def _collector(target: object) -> ModuleType | None:
    """The collector module for ``target``; ``None`` for a ``sqlite3.Connection`` (checked here).

    Raises ``TypeError`` for any other type. Only modules named in ``_COLLECTORS`` are ever imported.
    """
    if isinstance(target, sqlite3.Connection):
        return None
    for cls in type(target).__mro__:
        module = cls.__module__
        for prefix, collector in _COLLECTORS.items():
            if module == prefix or module.startswith(prefix + "."):
                return importlib.import_module(collector)
    raise TypeError(
        "tabrion.check.check expects a sqlite3.Connection, a psycopg.Connection or a tabrion.Database, "
        f"not {type(target).__name__}"
    )


def check(
    target: sqlite3.Connection | psycopg.Connection[Any] | Database,
    *,
    expect: Literal["runtime", "owner", "any"] = "runtime",
) -> list[Finding]:
    """Check a SQLite connection, a psycopg connection or a ``Database``; read only.

    Never raises for a missing privilege (an ``info`` finding "not checked" instead). ``expect="owner"``
    suppresses TAB-PG-003 and TAB-PG-009.
    """
    raise NotImplementedError("tabrion.check.check: implemented in TAB-017")
