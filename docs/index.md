# Tabrion

Tabrion is a Python library with safe defaults for applications that talk to SQLite or PostgreSQL directly. It runs
inside the application's process: no server, no service, no pool of its own, no stored credentials
([ADR 0001](adr/0001-standalone-library.md)).

!!! warning "Pre-release: M1 foundation"
    This is the API freeze milestone. Every public name exists with its final signature, but each function still
    raises `NotImplementedError`. The implementation follows in milestone M2 (Tabrion 0.1). Nothing is published
    on PyPI.

## Two layers in one distribution

| Layer | Modules | Needs |
|---|---|---|
| A, driver core | `tabrion.errors`, `tabrion.sql`, `tabrion.config`, `tabrion.sqlite`, `tabrion.check` | the standard library only |
| A, PostgreSQL helpers | `tabrion.postgres` | psycopg 3.2 or later (`tabrion[postgres]`) |
| A, test support | `tabrion.testing` | pytest (`tabrion[testing]`) |
| B, SQLAlchemy integration | `tabrion.Database`, `Profile`, `Transaction`, `Result`; `tabrion.migrations` | SQLAlchemy 2.0.30 or later (`tabrion[sqlalchemy]`); Alembic for `tabrion.migrations` (`tabrion[migrations]`) |

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

- [Installation](getting-started/installation.md)
- [Specification](specification.md): the behavior Tabrion guarantees.
- [API contract](api/contract.md): every public name with its signature (subject of the G-T1 review).
- [Architecture decisions](adr/README.md)
- [Program status](program/STATUS.md) and [open work](TODO.md)
