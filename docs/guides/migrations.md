# Migrations

Tabrion has **no PostgreSQL migration runner** and does not merge the existing ones
([ADR 0007](../adr/0007-no-shared-migration-runner.md)). What it has:

| Need | Use |
|---|---|
| SQLite, numbered Python steps, `PRAGMA user_version` | `tabrion.sqlite.migrate` ([SQLite](sqlite.md#schema-migrations-with-user_version)) |
| a Python application on SQLite or PostgreSQL with Alembic | Alembic to migrate, `tabrion.migrations` to **check** the state at start-up |
| statements that cannot run in a transaction | `tabrion.sql.split_statements` ([Splitting SQL scripts](sql.md)) |
| one migrating process at a time on PostgreSQL | `wait_advisory_lock` or `SessionLock` ([PostgreSQL](postgresql.md#advisory-locks)) |

This page covers the Alembic gate. It needs the extra `migrations` (`tabrion[migrations]`, which brings
`tabrion[sqlalchemy]` and Alembic). Contract: [section 5.5](../api/contract.md#55-health-diagnosis-inspection-schema-gate)
and implementation note I-57.

## An Alembic project on `tabrion.Database`

Alembic keeps its usual layout: `alembic.ini`, `migrations/env.py`, `migrations/versions/`. In `env.py`, open the
engine through `tabrion.Database`, so migrations run under the same profile (pragmas, timeouts, TLS rules) as the
application. On SQLite, use `render_as_batch=True`: SQLite's `ALTER TABLE` is limited, and batch mode rebuilds the
table instead.

<!-- docs-test: file=migrations/env.py -->
```python
from alembic import context

from tabrion import Database

with Database.from_env("DATABASE_URL") as db, db.engine.begin() as connection:
    context.configure(connection=connection, render_as_batch=db.dialect == "sqlite")
    with context.begin_transaction():
        context.run_migrations()
```

Two revisions:

<!-- docs-test: file=migrations/versions/0001_parts.py -->
```python
"""Create the parts table."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "parts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("material", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("parts")
```

<!-- docs-test: file=migrations/versions/0002_stock.py -->
```python
"""Add the stock column."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    with op.batch_alter_table("parts") as batch:
        batch.add_column(sa.Column("stock", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("parts") as batch:
        batch.drop_column("stock")
```

You migrate with Alembic, as always (`alembic upgrade head`; here through its Python API):

```python
import os

from alembic import command
from alembic.config import Config

os.environ["DATABASE_URL"] = "sqlite:///parts.db"
alembic_config = Config()
alembic_config.set_main_option("script_location", "migrations")
```

## Checking the state: `status`

`tabrion.migrations.status(database, script_location=...)` compares the database's version table with the script
directory. It only reads: it never runs `env.py`, never migrates, and creates nothing in a fresh database.

```pycon
>>> from tabrion import Database
>>> from tabrion.migrations import require_current, status
>>> db = Database.from_url("sqlite:///parts.db")
>>> status(db, script_location="migrations")
SchemaStatus(current=(), heads=('0002',), pending=('0001', '0002'), is_current=False, unknown=())
>>> command.upgrade(alembic_config, "0001")
>>> status(db, script_location="migrations").pending
('0002',)
>>> command.upgrade(alembic_config, "head")
>>> status(db, script_location="migrations")
SchemaStatus(current=('0002',), heads=('0002',), pending=(), is_current=True, unknown=())
```

| Field | Meaning |
|---|---|
| `current` | revisions recorded in the database (empty: not versioned yet) |
| `heads` | the heads of the script directory |
| `pending` | revisions still to apply, in the order Alembic applies them |
| `unknown` | revisions in the database that the script directory does not know: the database is **newer** than the code |
| `is_current` | nothing pending and nothing unknown |

`version_table` and `version_table_schema` select a version table other than `alembic_version` (for PostgreSQL, one
in a schema of its own).

## The start-up gate: `require_current`

`require_current` takes the same arguments and raises `SchemaNotCurrent` unless the schema is current. Call it when
the application starts, before it serves anything:

```pycon
>>> require_current(db, script_location="migrations").is_current
True
>>> command.downgrade(alembic_config, "0001")
>>> require_current(db, script_location="migrations")
Traceback (most recent call last):
  ...
tabrion.errors.SchemaNotCurrent: migrations.not_current (current=0001, head=0002)
>>> db.close()
```

It works under `Profile.readonly()` too, so a read-only tool can refuse a database of the wrong version.

## Things to know

- `status` imports every revision file of `versions/`, as Alembic does: module-level code in them runs. The script
  directory must be trusted code, and the import cost is paid on every call; cache the result where you call it
  often.
- Only `<script_location>/versions` is read; `version_locations` from `alembic.ini` are not applied.
- A broken script directory (a cycle, a missing `down_revision`, a syntax error) raises
  `ConfigurationError("migrations.revisions")`; a `script_location` that is not a directory raises
  `ConfigurationError("migrations.script_location")`. Neither names the path.
- Alembic itself writes through `db.engine`, outside Tabrion's transaction modes; on SQLite its transactions begin
  deferred. Run migrations from one process at a time.
