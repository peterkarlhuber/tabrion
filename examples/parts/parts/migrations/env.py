"""Alembic environment of the parts application.

``parts.db.upgrade`` passes a connection of the application's ``tabrion.Database`` in ``config.attributes``. The
``alembic`` command line (``alembic.ini`` next to the package) opens a ``Database`` from ``sqlalchemy.url`` instead,
with the same ``database.transaction()``. Alembic sees a transaction already begun and neither commits nor rolls it
back; the ``with`` block does.
``tabrion.migrations.status`` never loads this file.
"""

from __future__ import annotations

from alembic import context
from parts.schema import metadata

from tabrion import Database


def run(connection: object) -> None:
    context.configure(connection=connection, target_metadata=metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


given = context.config.attributes.get("connection")
if given is not None:
    run(given)
else:
    url = context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set sqlalchemy.url in alembic.ini or run parts.db.upgrade")
    with Database.from_url(url) as database, database.transaction() as transaction:
        run(transaction.connection)
