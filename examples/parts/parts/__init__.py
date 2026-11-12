"""Parts reference application, part 1: a portable schema with Alembic (TABRION RA-A-1).

Layer B (``tabrion.Database``) and ``tabrion.migrations``: one schema, one set of Alembic migrations, the same result
on SQLite and PostgreSQL, and ``require_current`` as the start-up gate. ``parts.catalog`` (RA-A-2) holds the
functions: CRUD, status changes with an optimistic lock, relation queries.
"""

from parts.db import downgrade, open_database, require_current, status, upgrade
from parts.schema import STATUSES, metadata

__all__ = ["STATUSES", "downgrade", "metadata", "open_database", "require_current", "status", "upgrade"]
