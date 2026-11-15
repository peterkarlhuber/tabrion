# Migrations

Tabrion has **no PostgreSQL migration runner** and does not merge the existing ones
([ADR 0007](../adr/0007-no-shared-migration-runner.md)). What it has:

| Need | Use |
|---|---|
| SQLite, numbered Python steps, `PRAGMA user_version` | `tabrion.sqlite.migrate` ([SQLite](sqlite.md#schema-migrations-with-user_version)) |
| a Python application on SQLite or PostgreSQL with Alembic | Alembic to migrate, `tabrion.migrations` to **check** the state at start-up |
| statements that cannot run in a transaction | `tabrion.sql.split_statements` ([Splitting SQL scripts](sql.md)) |
| one migrating process at a time on PostgreSQL | `wait_advisory_lock` or `SessionLock` ([PostgreSQL](postgresql.md#advisory-locks)) |
| a reviewed DDL plan from the schema you have to the one you want, or its Alembic revision text (0.3, experimental) | `tabrion.plan` ([below](#schema-change-plans-03-experimental)) |

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

## Schema change plans (0.3, experimental)

`tabrion.plan` (extra `sqlalchemy`; a plan with column steps also `migrations`; contract
[section 5.13](../api/contract.md#513-schema-change-plans-tabrionplan-since-03-experimental-extra-sqlalchemy-tab-xw-4))
computes the change from the schema a database has to the schema you want, as a reviewed list of DDL statements.
`plan` never runs them. On a DEV or TEST database without migration management, `execute_plan` runs the confirmed
change ([below](#running-a-confirmed-change-execute_plan)); an Alembic project gets the plan as revision **text**
that you add to the project and run with Alembic yourself
([ADR 0015](../adr/0015-crdbx-write-and-ddl-primitives.md) sections 2 and 3).

The input is two level-2 snapshots of [`tabrion.schema`](profiles-and-schema.md): the database as it is and the
target model, which you build with `dataclasses.replace` (or `SchemaSnapshot.from_json`):

```pycon
>>> import dataclasses
>>> import sqlite3
>>> from tabrion import Database
>>> from tabrion import plan as tp
>>> from tabrion import schema as ts
>>> from tabrion.errors import PlanRefused
>>> with Database.from_url("sqlite:///plan.db") as shop:
...     _ = shop.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, code VARCHAR(10))")
...     with shop.inspector() as inspector:
...         current = ts.snapshot(inspector, level=2)
...     markers = tp.migration_markers(shop)
>>> markers
()
>>> parts = current.table("main.parts")
>>> qty = ts.ColumnShape("qty", "INTEGER", True, default="0")
>>> target = dataclasses.replace(current, tables=(dataclasses.replace(parts, columns=(*parts.columns, qty)),))
>>> change = tp.plan(current, target, server_version=sqlite3.sqlite_version_info)
>>> for step in change.steps:
...     print(step.index, step.operation, step.target, [risk.value for risk in step.risks])
0 add_column main.parts ['additive', 'data_changing']
>>> print(change.steps[0].statements[0].sql)
ALTER TABLE parts ADD COLUMN qty INTEGER DEFAULT (0)
```

`migration_markers` tells you whether the database is under migration management (an `alembic_version` table, the
version tables of Nodestro and Axentra, or SQLite's `PRAGMA user_version`); a managed database gets a revision, never
direct DDL (ADR 0015 section 3).

Every step carries its **risk classes**: `additive`, `data_changing` (a type change, `NOT NULL` on existing rows, a
backfilled default), `destructive` (a drop or a narrowing type), `rebuild` (a SQLite table rebuild), `locking`
(PostgreSQL: the step blocks writes on an existing table) and `non_transactional` (`CREATE INDEX CONCURRENTLY`, with
`plan(..., concurrent_indexes=True)`). Show them before anyone confirms a plan. A change SQLite cannot make in place
becomes one `rebuild_table` step: the rows go to a holder table, the table is created again from the target and the
rows come back, then its indexes and triggers, and every view is read once (`SELECT * FROM <view> LIMIT 0`), so a
view the rebuild broke fails the transaction. It needs the foreign keys off, as
[above](#why-the-foreign-keys-are-off-the-cascade): with them on, its `DROP TABLE` deletes the rows of child tables
through `ON DELETE CASCADE`, and `PRAGMA foreign_keys` cannot change inside a transaction. Pass `server_version`
(`sqlite3.sqlite_version_info` of the engine that runs the plan) so that a plain column drop runs in place, where
SQLite checks views and triggers itself:

```pycon
>>> shorter = dataclasses.replace(parts, columns=(parts.columns[0], ts.ColumnShape("code", "VARCHAR(5)", True)))
>>> rebuild = tp.plan(current, dataclasses.replace(current, tables=(shorter,)))
>>> [(step.operation, [risk.value for risk in step.risks]) for step in rebuild.steps]
[('rebuild_table', ['data_changing', 'destructive', 'rebuild'])]
```

The plan is bound to both fingerprints and to its exact SQL by `plan_hash`; `MigrationPlan.from_json` refuses a plan
whose content no longer matches its hash. That proves **integrity, not authenticity**: anyone can compute the hash of
a plan they wrote, so never take a plan from an untrusted source (a project file, a URL, an import); keep it where
only your code writes it. Each step's risk classes must include those its operation implies (a drop is
`destructive`), so an edited plan cannot pass a drop off as `additive`; but only planning again proves the
statements: before a plan runs, plan again from a fresh snapshot and the target you hold, compare the new
`plan_hash` with the one the user confirmed, and confirm the risk classes of the new plan.

`revision_text` writes the plan as an Alembic revision. The docstring is fixed and every caller value is a string
literal, so a message cannot become code; `upgrade()` refuses another engine and runs the previewed statements one
by one; `downgrade()` raises `NotImplementedError` (plan and review the reverse change instead):

```pycon
>>> text = tp.revision_text(change, revision="0004", down_revision="0003", message="parts: add qty")
>>> print(text[text.index("def upgrade") :])
def upgrade() -> None:
    if op.get_context().dialect.name != dialect:
        raise RuntimeError("this revision was planned for another database engine")
    # step 0: add_column (additive, data_changing)
    op.execute(sa.text('ALTER TABLE parts ADD COLUMN qty INTEGER DEFAULT (0)'))
<BLANKLINE>
<BLANKLINE>
def downgrade() -> None:
    raise NotImplementedError("Tabrion writes no downgrade: plan the reverse change and review it")
<BLANKLINE>
```

Write the text into `versions/` yourself and review it like any revision. Tabrion never reads the project, never
runs `env.py`, an Alembic command or a revision. The revision of a SQLite plan with a rebuild or a table drop
refuses to start while foreign keys are on and runs `PRAGMA foreign_key_check` before the commit; run it with the
migration profile of [`env.py`](#an-alembic-project-on-tabriondatabase). The revision does not check the source
fingerprint: apply it to the database it was planned for, soon after the preview. A `CONCURRENTLY` step commits
every step before it, so the plan puts those steps first (drops) and last (creations).

A plan never drops what other SQLite schema text still names: a column or table that a view or trigger names
refuses the plan (`dependent_object`). On PostgreSQL the server refuses such a drop for views, policies and
`UPDATE OF` triggers, but a PL/pgSQL function body that names the column breaks only when it runs: check the
functions before you drop a column or table.

```pycon
>>> with Database.from_url("sqlite:///plan.db") as shop:
...     _ = shop.execute("CREATE VIEW part_codes AS SELECT id, code FROM parts")
...     with shop.inspector() as inspector:
...         with_view = ts.snapshot(inspector, level=2)
>>> parts_now = with_view.table("main.parts")
>>> no_code = dataclasses.replace(parts_now, columns=parts_now.columns[:1])
>>> try:
...     tp.plan(with_view, dataclasses.replace(with_view, tables=(no_code,)), server_version=sqlite3.sqlite_version_info)
... except PlanRefused as error:
...     print(dict(error.details))
{'reason': 'dependent_object', 'table': 'main.parts', 'object': 'main.part_codes'}
```

**What a plan refuses** (`PlanRefused`, reason `unsupported_feature`, ADR 0015 D-5): any change while the PostgreSQL
database has an event trigger; changing or dropping a PostgreSQL table that is partitioned or a partition, inherits,
has rules, is a foreign, unlogged or typed table, has storage parameters, a non-default replica identity, a domain
column or a `NO INHERIT` check; a table whose trigger or policy function is not PL/pgSQL or SQL; creating or
changing triggers, policies or row-level security (a plan keeps the existing ones of a table it changes); and a SQLite
rebuild of a table whose `CREATE TABLE` holds what the shape does not (`STRICT`, `WITHOUT ROWID`, `AUTOINCREMENT`,
`ON CONFLICT`, a column collation):

```pycon
>>> with Database.from_url("sqlite:///strict.db") as strict:
...     _ = strict.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL) STRICT")
...     with strict.inspector() as inspector:
...         tags_now = ts.snapshot(inspector, level=2)
>>> tags = tags_now.table("main.tags")
>>> loose = dataclasses.replace(tags, columns=(tags.columns[0], ts.ColumnShape("name", "TEXT", True)))
>>> try:
...     tp.plan(tags_now, dataclasses.replace(tags_now, tables=(loose,)))
... except PlanRefused as error:
...     print(error.code, dict(error.details))
plan.refused {'reason': 'unsupported_feature', 'table': 'main.tags', 'feature': 'strict'}
```

Residual risks (contract I-124 j): functions called by a trigger or policy function are not in the fingerprint; a
function can change after a drift check because it cannot be locked; function and table owners, privileges,
publications, statistics and PostgreSQL views are not in the fingerprint either; a SQLite rebuild gives a table
without an `INTEGER PRIMARY KEY` new `rowid` values; a version table under another name than `alembic_version` is
not found by `migration_markers`.

### Running a confirmed change: `execute_plan`

`execute_plan` (TAB-XW-5, contract I-125) runs a change on a database that is **not** under migration management and
that you declare as `"dev"` or `"test"`; the declaration has no default and comes from your own stored settings,
never from a request: it is a guard rail behind your own production lock, not a boundary. The executor never takes a
plan from you. It takes the **target** model and the confirmation of the preview: the `plan_hash` the user saw, the
fingerprint of the snapshot the preview was made from and the risk classes the user accepted. Inside its own
transaction it takes the snapshot again, checks the fingerprint (`SchemaDrift` when the schema changed since the
preview), plans again and runs only a plan with the confirmed hash (`PlanMismatch("plan.hash_mismatch")` otherwise):

```pycon
>>> from tabrion.errors import PlanMismatch, SchemaDrift
>>> with Database.from_url("sqlite:///plan.db") as shop:
...     with shop.inspector() as inspector:
...         preview_source = ts.snapshot(inspector, level=2)
...     parts_now = preview_source.table("main.parts")
...     wanted = dataclasses.replace(
...         preview_source, tables=(dataclasses.replace(parts_now, columns=(*parts_now.columns, qty)),)
...     )
...     preview = tp.plan(preview_source, wanted)  # shown to the user, who confirms its hash and risks
...     result = tp.execute_plan(
...         shop,
...         wanted,
...         plan_hash=preview.plan_hash,
...         expected_fingerprint=ts.fingerprint(preview_source),
...         environment="dev",
...         allow=preview.risks,
...         source=preview_source,
...     )
>>> result.steps_run, result.fingerprint_after == ts.fingerprint(wanted)
(1, True)
```

The same confirmation does not run twice: the schema has changed since the preview, so the fingerprint no longer
matches, and nothing runs. With `source=` (the preview's snapshot) the error names the differences:

```pycon
>>> with Database.from_url("sqlite:///plan.db") as shop:
...     try:
...         tp.execute_plan(
...             shop,
...             wanted,
...             plan_hash=preview.plan_hash,
...             expected_fingerprint=ts.fingerprint(preview_source),
...             environment="dev",
...             allow=preview.risks,
...             source=preview_source,
...         )
...     except SchemaDrift as error:
...         print(error.code, [str(difference) for difference in error.differences])
plan.drift ['column_added: main.parts.qty']
```

Before the first statement the executor also refuses a database with migration markers (`PlanRefused`, reason
`migration_managed`), a risk class the user did not accept (`risk_not_allowed`) and an environment other than
`"dev"` or `"test"` (`environment`). `dry_run=True` runs the transactional steps and rolls them back.

- **SQLite:** foreign keys are turned off before `BEGIN IMMEDIATE` (the executor refuses to start, reason
  `foreign_keys_on`, if they stay on), `trusted_schema` off and defensive mode on (Python 3.12 or later; without it a
  plan that recreates a trigger is refused); each statement runs on its own on your connection, under your
  authorizer. Before the commit `PRAGMA foreign_key_check` and `PRAGMA integrity_check` must find nothing, else
  everything rolls back (`PlanFailed`, `check`, `violations`). The settings are restored afterwards.
- **PostgreSQL:** one `READ COMMITTED` transaction with `SET LOCAL lock_timeout` (`lock_timeout=5.0` seconds by
  default). The executor locks every existing table a step changes, then fingerprints and plans again under the
  locks, so concurrent DDL cannot land between the check and the first step. A lock that is not granted in time
  raises `TransientError` with `details["step"]`, after a rollback. `CREATE`/`DROP INDEX CONCURRENTLY` steps run
  alone, outside the transaction and in plan order; a failure there leaves the database partly changed
  (`PlanFailed`, state `partial`; a failed `CREATE INDEX CONCURRENTLY` leaves an invalid index to drop).
- A failing step raises `PlanFailed` with `step`, `completed` and `state` (`rolled_back`, `partial`, or `unknown`
  when the connection was lost during the `COMMIT`); the error's `original` is the step's own error, without values.
  The executor never retries.

A function cannot be locked: a trigger or policy function changed after the check, and functions those functions
call, are residual risks (contract I-124 j).

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
