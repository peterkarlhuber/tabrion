"""Alembic environment of the plain SQLAlchemy baseline.

``parts_baseline.db.upgrade`` passes a connection of the application's engine in ``config.attributes``. The
``alembic`` command line (``alembic.ini`` next to the package) creates an engine from ``sqlalchemy.url`` instead.
"""

from __future__ import annotations

from alembic import context
from parts_baseline.db import create_engine
from parts_baseline.schema import metadata
import sqlalchemy as sa


def run(connection: sa.Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


given = context.config.attributes.get("connection")
if given is not None:
    run(given)
else:
    url = context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set sqlalchemy.url in alembic.ini or run parts_baseline.db.upgrade")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()
