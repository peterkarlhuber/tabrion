"""Read-only Alembic schema gate (layer B, extra ``migrations``).

Contract: ``docs/api/contract.md`` section 5.5 and implementation note I-57 (TAB-019). ``status`` reads the script
directory with ``alembic.script.ScriptDirectory`` and the database's version table with ``MigrationContext`` on a
``Database.read()`` connection. It never runs ``env.py`` (no application code from it, no side effects) and never
migrates; migrating stays ``alembic upgrade``. Only ``<script_location>/versions`` is read: ``version_locations``
and ``recursive_version_locations`` of ``alembic.ini`` are not (TODO TB-066).

Every error is raised outside an ``except`` block, so it has neither ``__cause__`` nor ``__context__`` (I-39).
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

_BROKEN_SCRIPTS = (RevisionError, CommandError, KeyError, TypeError, ImportError, SyntaxError)
"""What Alembic raises for a script directory or version table it cannot resolve (review L-1 of #14); ``TypeError``
for a ``revision`` or ``down_revision`` of the wrong type (re-review DL-1)."""


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
    script: ScriptDirectory | None = None
    if isinstance(location, str) and os.path.isdir(location):
        try:
            # Reads the revision files of versions/ (imports them, as Alembic does); env.py is never loaded (I-57).
            script = ScriptDirectory(location)
        except CommandError:
            script = None
    if script is None:
        raise ConfigurationError("migrations.script_location")  # the message never names the path
    return script


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
    graph: tuple[tuple[str, ...], list[str]] | None = None
    try:
        # Building the revision map imports every revision file and resolves the graph.
        heads = tuple(sorted(set(script.get_heads())))
        # From the heads down to the base; reversed, this is the order in which Alembic applies them.
        ordered = list(dict.fromkeys(revision.revision for revision in script.walk_revisions("base", "heads")))
        graph = heads, ordered
    except _BROKEN_SCRIPTS:
        graph = None  # a cycle, a missing or mistyped parent, a branch label as parent, a broken file
    if graph is None:
        raise ConfigurationError("migrations.revisions")
    heads, ordered = graph
    with database.read() as transaction:
        context = MigrationContext.configure(
            connection=transaction.connection, opts={"version_table": table, "version_table_schema": schema}
        )
        current = tuple(sorted(context.get_current_heads()))
    known = set(ordered)
    unknown = tuple(revision for revision in current if revision not in known)
    pending: tuple[str, ...] | None = ()  # the database is newer than this script directory: nothing can be planned
    if not unknown:
        try:
            # Ancestors through down_revision and depends_on, as Alembic's own upgrade plan walks them.
            applied = {r.revision for r in script.iterate_revisions(current, "base")} if current else set()
            pending = tuple(revision for revision in reversed(ordered) if revision not in applied)
        except _BROKEN_SCRIPTS:
            pending = None  # the version table holds a revision and its own ancestor
    if pending is None:
        raise ConfigurationError("migrations.revisions")
    return SchemaStatus(
        current=current,
        heads=heads,
        pending=pending,
        # Through pending, not by comparing with heads: a head that another branch depends_on is covered by the
        # dependent revision and never written to the version table (review H-1 of #14).
        is_current=not unknown and not pending,
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
