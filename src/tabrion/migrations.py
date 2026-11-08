"""Read-only Alembic schema gate (layer B, extra ``migrations``).

Contract: ``docs/api/contract.md`` section 5.5. Stub for the API freeze G-T1; the implementation follows in TAB-019.
``status`` never runs ``env.py`` (no application code, no side effects) and never migrates.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import TYPE_CHECKING, Any

try:
    import alembic as _alembic  # noqa: F401  # the extra "migrations"
except ImportError:
    raise ImportError('tabrion.migrations needs the extra "migrations": pip install "tabrion[migrations]"') from None

if TYPE_CHECKING:
    from tabrion._sa.database import Database

__all__ = ["SchemaStatus", "require_current", "status"]


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    current: tuple[str, ...]
    """Revisions in the database (``alembic_version``); empty means unversioned."""
    heads: tuple[str, ...]
    """Target heads of the script directory."""
    pending: tuple[str, ...]
    """Revisions to apply, in order."""
    is_current: bool
    unknown: tuple[str, ...]
    """Database revisions the script directory does not know (a newer version)."""


def status(
    database: Database,
    *,
    script_location: str | os.PathLike[str],
    version_table: str = "alembic_version",
    version_table_schema: str | None = None,
) -> SchemaStatus:
    """The schema status on a ``read()`` connection, without running ``env.py``."""
    raise NotImplementedError("tabrion.migrations.status: implemented in TAB-019")


def require_current(database: Database, **kw: Any) -> SchemaStatus:
    """Like ``status``; raises ``SchemaNotCurrent`` (details ``current``, ``head``) unless current."""
    raise NotImplementedError("tabrion.migrations.require_current: implemented in TAB-019")
