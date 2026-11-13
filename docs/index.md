<div class="tabrion-hero" markdown>
![Tabrion](assets/branding/tabrion-logo.png#only-light)
![Tabrion](assets/branding/tabrion-logo-on-dark.png#only-dark)
</div>

# Tabrion

Tabrion is a Python library with safe defaults for applications that talk to SQLite or PostgreSQL directly. It runs
inside the application's process: no server, no service, no pool of its own, no stored credentials
([ADR 0001](adr/0001-standalone-library.md)).

!!! warning "Pre-release: Tabrion 0.1 in development"
    The 0.1 modules are implemented (milestone M2); the 0.2 names are frozen in the API but raise
    `NotImplementedError`. Nothing is published on PyPI: Tabrion is installed from Git, pinned to a commit SHA
    ([Installation](getting-started/installation.md)).

## Two layers in one distribution

| Layer | Modules | Needs |
|---|---|---|
| A, driver core | `tabrion.errors`, `tabrion.sql`, `tabrion.config`, `tabrion.sqlite`, `tabrion.check` | the standard library only |
| A, PostgreSQL helpers | `tabrion.postgres` | psycopg 3.2 or later (`tabrion[postgres]`) |
| A, test support | `tabrion.testing` | pytest (`tabrion[testing]`) |
| B, SQLAlchemy integration | `tabrion.Database`, `Profile`, `Transaction`, `Result` (0.2 Studio APIs); `tabrion.migrations` | SQLAlchemy 2.0.30 or later (`tabrion[sqlalchemy]`); Alembic for `tabrion.migrations` (`tabrion[migrations]`) |

Layer B applies the layer A profile to the same DBAPI connection through SQLAlchemy events; layer A never imports
layer B ([ADR 0002](adr/0002-two-layers-one-distribution.md)).

## What the defaults do

- **SQLite:** write transactions with `BEGIN IMMEDIATE`, WAL, `foreign_keys=ON`, `synchronous=FULL`,
  `trusted_schema=OFF`, new files with mode 0600 created before opening, symlinks rejected
  ([ADR 0005](adr/0005-sqlite-profile.md)).
- **PostgreSQL:** finite statement, lock and idle timeouts, an `application_name`, UTC; no session-wide security
  settings; no retry of a statement once sending has started ([ADR 0006](adr/0006-postgres-session-profile.md)).
- **Errors:** a stable classification by SQLSTATE and safe messages without parameter values or `DETAIL` lines
  ([ADR 0004](adr/0004-error-model.md)).

## Where to read on

- [Installation](getting-started/installation.md) and the [Quickstart](getting-started/quickstart.md)
- The guides: [SQLite](guides/sqlite.md), [PostgreSQL](guides/postgresql.md),
  [Database and Profile](guides/database.md), [Transactions](guides/transactions.md), [Errors](guides/errors.md),
  [Migrations](guides/migrations.md), [Diagnosis](guides/check.md), [Testing](guides/testing.md),
  [Portability](guides/portability.md), [Security](guides/security.md) and
  [Troubleshooting](guides/troubleshooting.md). Every code example in them runs in the test suite.
- [Specification](specification.md): the behavior Tabrion guarantees.
- [API contract](api/contract.md): every public name with its signature (subject of the G-T1 review).
- [Architecture decisions](adr/README.md)
- [Program status](program/STATUS.md) and [open work](TODO.md)
