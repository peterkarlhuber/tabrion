"""Read-only Alembic schema gate (layer B, extra ``migrations``).

Contract: ``docs/api/contract.md`` section 5.5 and implementation note I-57 (TAB-019). ``status`` reads the script
directory with ``alembic.script.ScriptDirectory`` and the database's version table with ``MigrationContext`` on a
``Database.read()`` connection. It never runs ``env.py`` (no application code from it, no side effects) and never
migrates; migrating stays ``alembic upgrade``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

try:
    import alembic as _alembic  # noqa: F401  # the extra "migrations"
except ImportError:
    raise ImportError('tabrion.migrations needs the extra "migrations": pip install "tabrion[migrations]"') from None

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError

from tabrion._sa.database import Database
from tabrion.errors import ConfigurationError, SchemaNotCurrent

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


def _script_directory(script_location: object) -> ScriptDirectory:
    if not isinstance(script_location, str | os.PathLike):
        raise TypeError("script_location must be a str or os.PathLike")
    location = os.fspath(script_location)
    if not isinstance(location, str) or not os.path.isdir(location):
        raise ConfigurationError("migrations.script_location")  # the message never names the path
    try:
        # Reads the revision files of versions/ (imports them, as Alembic does); env.py is never loaded (I-57).
        return ScriptDirectory(location)
    except CommandError:
        raise ConfigurationError("migrations.script_location") from None


def _name(value: object, field: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or value == "":
        raise TypeError(f"{field} must be a non-empty str" + (" or None" if optional else ""))
    return value


def status(
    database: Database,
    *,
    script_location: str | os.PathLike[str],
    version_table: str = "alembic_version",
    version_table_schema: str | None = None,
) -> SchemaStatus:
    """The schema status on a ``read()`` connection, without running ``env.py``."""
    if not isinstance(database, Database):
        raise TypeError(f"database must be a tabrion.Database, not {type(database).__name__}")
    table = _name(version_table, "version_table", optional=False)
    schema = _name(version_table_schema, "version_table_schema", optional=True)
    script = _script_directory(script_location)
    try:
        heads = tuple(sorted(script.get_heads()))
        # From the heads down to the base; reversed, this is the order in which Alembic applies them.
        ordered = [revision.revision for revision in script.walk_revisions("base", "heads")]
    except (RevisionError, CommandError):
        raise ConfigurationError("migrations.revisions") from None  # a broken graph, such as a cycle
    with database.read() as transaction:
        context = MigrationContext.configure(
            connection=transaction.connection, opts={"version_table": table, "version_table_schema": schema}
        )
        current = tuple(sorted(context.get_current_heads()))
    known = set(ordered)
    unknown = tuple(revision for revision in current if revision not in known)
    if unknown:
        pending: tuple[str, ...] = ()  # the database is newer than this script directory: nothing can be planned
    else:
        applied = {revision.revision for revision in script.iterate_revisions(current, "base")} if current else set()
        pending = tuple(revision for revision in reversed(ordered) if revision not in applied)
    return SchemaStatus(
        current=current,
        heads=heads,
        pending=pending,
        is_current=not unknown and set(current) == set(heads),
        unknown=unknown,
    )


def require_current(database: Database, **kw: Any) -> SchemaStatus:
    """Like ``status``; raises ``SchemaNotCurrent`` (details ``current``, ``head``) unless current."""
    result = status(database, **kw)
    if not result.is_current:
        raise SchemaNotCurrent(
            "migrations.not_current", details={"current": ",".join(result.current), "head": ",".join(result.heads)}
        )
    return result
