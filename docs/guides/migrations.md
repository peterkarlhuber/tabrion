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
engine through `tabrion.Database`, so migrations run under Tabrion's TLS rules and pragmas, but with a **profile of
their own**, derived from the application's:

- **SQLite: foreign keys off.** SQLite's `ALTER TABLE` is limited, so Alembic's batch mode (`render_as_batch=True`)
  rebuilds a table for `alter_column`, `drop_column` and `recreate="always"`: it copies the rows to a new table and
  runs `DROP TABLE` on the old one. With `foreign_keys=ON` (the default profile), that `DROP TABLE` deletes the rows
  first, which fires `ON DELETE CASCADE` in child tables and silently deletes their rows (or fails on an immediate
  foreign key). `PRAGMA foreign_keys` has no effect inside a transaction, so it must be off **before** the migration
  transaction begins: `SQLiteProfile(foreign_keys=False)` sets it when the connection opens. Run
  `PRAGMA foreign_key_check` after the migrations, before the commit, so a migration that broke a reference fails
  instead of committing ([`tabrion.sqlite.foreign_key_check`](sqlite.md) does the same on a `sqlite3` connection).
- **PostgreSQL: longer timeouts.** The default profile sets `statement_timeout` to 30 s and `lock_timeout` to 10 s
  for every session. A long index build or a table rewrite would then fail with the error code `timeout`. Give
  migrations `statement_timeout_ms=0` (no limit) and a lock wait that fits your deployment window.

<!-- docs-test: file=migrations/env.py -->
```python
import dataclasses

from alembic import context

from tabrion import Database, Profile

base = Profile()  # or the application's profile
migration_profile = dataclasses.replace(
    base,
    sqlite=dataclasses.replace(base.sqlite, foreign_keys=False),  # set at connect, outside any transaction
    postgres=dataclasses.replace(base.postgres, statement_timeout_ms=0, lock_timeout_ms=60_000),
    pool_size=1,
)

with Database.from_env("DATABASE_URL", profile=migration_profile) as db, db.engine.begin() as connection:
    context.configure(connection=connection, render_as_batch=db.dialect == "sqlite")
    with context.begin_transaction():
        context.run_migrations()
    if db.dialect == "sqlite":
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        if violations:
            raise RuntimeError(f"foreign key violations after migrating: {violations[:5]}")
```

Three revisions: a parent table, a child table with `ON DELETE CASCADE`, and a batch change that rebuilds the parent:

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

<!-- docs-test: file=migrations/versions/0002_moves.py -->
```python
"""Create the stock_moves table."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "stock_moves",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("part_id", sa.Integer, sa.ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stock_moves")
```

<!-- docs-test: file=migrations/versions/0003_material.py -->
```python
"""Widen parts.material (SQLite rebuilds the table)."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    with op.batch_alter_table("parts") as batch:
        batch.alter_column("material", type_=sa.String(80), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("parts") as batch:
        batch.alter_column("material", type_=sa.String(40), existing_nullable=False)
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

### Why the foreign keys are off: the cascade

Here is the same rebuild under the **default** profile (`foreign_keys=ON`), on a database of its own. The parent is
rebuilt, and the child row is gone without an error:

```pycon
>>> from alembic.migration import MigrationContext
>>> from alembic.operations import Operations
>>> from tabrion import Database
>>> with Database.from_url("sqlite:///cascade.db") as risky:
...     with risky.engine.begin() as connection:
...         _ = connection.exec_driver_sql("CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT)")
...         _ = connection.exec_driver_sql(
...             "CREATE TABLE stock_moves (id INTEGER PRIMARY KEY,"
...             " part_id INTEGER NOT NULL REFERENCES parts (id) ON DELETE CASCADE)"
...         )
...         _ = connection.exec_driver_sql("INSERT INTO parts VALUES (1, 'steel')")
...         _ = connection.exec_driver_sql("INSERT INTO stock_moves VALUES (1, 1)")
...     with risky.engine.begin() as connection:
...         with Operations(MigrationContext.configure(connection)).batch_alter_table("parts", recreate="always"):
...             pass
...     risky.fetch_scalar("SELECT count(*) FROM parts"), risky.fetch_scalar("SELECT count(*) FROM stock_moves")
(1, 0)
```

With the migration profile of `env.py`, the same kind of rebuild (revision `0003`) keeps the child rows; see the next
section.

## Checking the state: `status`

`tabrion.migrations.status(database, script_location=...)` compares the database's version table with the script
directory. It only reads: it never runs `env.py`, never migrates, and creates nothing in a fresh database.

```pycon
>>> from tabrion.migrations import require_current, status
>>> db = Database.from_url("sqlite:///parts.db")
>>> status(db, script_location="migrations")
SchemaStatus(current=(), heads=('0003',), pending=('0001', '0002', '0003'), is_current=False, unknown=())
>>> command.upgrade(alembic_config, "0002")
>>> status(db, script_location="migrations").pending
('0003',)
>>> with db.transaction() as tx:
...     _ = tx.execute("INSERT INTO parts (id, material) VALUES (1, 'steel')")
...     _ = tx.execute("INSERT INTO stock_moves (part_id, quantity) VALUES (1, 5)")
>>> command.upgrade(alembic_config, "head")
>>> status(db, script_location="migrations")
SchemaStatus(current=('0003',), heads=('0003',), pending=(), is_current=True, unknown=())
>>> db.fetch_scalar("SELECT count(*) FROM stock_moves")  # revision 0003 rebuilt parts; the move survived
1
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
>>> command.downgrade(alembic_config, "0002")
>>> require_current(db, script_location="migrations")
Traceback (most recent call last):
  ...
tabrion.errors.SchemaNotCurrent: migrations.not_current (current=0002, head=0003)
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
- Keep the migration profile for migrations only. The application keeps `foreign_keys=ON` and its timeouts; a
  migration `Database` is opened, used and closed by `env.py`.
