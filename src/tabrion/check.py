"""Read-only configuration and security checks with stable finding codes (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 7; the codes are listed in ``docs/specification.md`` section 9.
Stub for the API freeze G-T1; the implementation follows in TAB-017. The PostgreSQL and layer-B checks import
``tabrion.postgres`` and layer B lazily inside the function, so this module stays standard-library only.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import sqlite3
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
