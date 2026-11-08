# ADR 0002: Two layers in one distribution, extras, import isolation

- **Status:** Accepted (2026-11-07, plan approval G-A; T-E2 confirmed).
- **Plan reference:** TAB-ADR-0002 (planning document 10)

## Context

- The existing platforms use psycopg and sqlite3 directly. Nodestro ADR-0002
  (`nodestro-studio/docs/adr/0002-storage-backends.md:30, 77`) and Axentra ADR 0003
  (`axentra/docs/adr/0003-postgres-psycopg-raw-sql.md:8`) reject an ORM and SQLAlchemy respectively.
- New applications benefit from SQLAlchemy Core and Alembic.
- The probes show that the SQLAlchemy defaults are unsafe: with pysqlite, 4 of 5 updates are lost (q.py); on
  PostgreSQL every timeout is 0 and parameters appear in errors (r.py).

## Decision

- One package `tabrion` with two layers:
  - **Layer A (driver core):** `tabrion.errors`, `tabrion.sql`, `tabrion.config`, `tabrion.sqlite`, `tabrion.check`
    use only the standard library. `tabrion.postgres` uses only psycopg >=3.2,<4, without `psycopg_pool`.
    `tabrion.testing` uses pytest.
  - **Layer B (SQLAlchemy integration):** `tabrion.Database`, `Profile`, `Transaction`, `Result` and
    `tabrion.migrations`, a thin shell around a SQLAlchemy engine.
- Layer B applies the layer A functions through the SQLAlchemy events `connect`, `begin` and `reset` to the **same**
  DBAPI connection (specification section 3.1).
  - There is no second connection, no pool of its own and no adapter between the layers.
  - Layer A never imports layer B.
- Extras (K-05, N-08): `postgres`, `sqlalchemy` (SQLAlchemy >=2.0.30,<2.2), `migrations` (implies `sqlalchemy`,
  Alembic >=1.13,<2), `testing`, `all`. Every combination installs.
- Layer B names are exported lazily through PEP 562. Without SQLAlchemy, `tabrion.Database` raises an `ImportError`
  with an installation hint.

## Rationale

- A SQLite application (measurements at the edge, Nodestro Desktop) installs nothing besides `tabrion`.
- The same profile function acts at two entry points, which prevents drift between the layers.
- One package instead of two avoids two release cascades for tightly coupled code.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| SQLAlchemy layer only (Tabrion concept V1.0) | The existing platforms would have to adopt SQLAlchemy ([ADR 0003](0003-existing-platforms-layer-a.md)); the desktop bundle grows |
| Driver-level layer only | New applications lose Core expressions, Alembic and the inspector; Studio would need its own introspection |
| Two distributions (`tabrion-core`, `tabrion`) | Double versioning and pins without benefit; extras do the same |
| Adapter or capabilities registry, own protocol | "Architecture in stock", already rejected in the Tabrion report |

## Consequences

- Code that uses `db.engine.connect()` directly gets the connection profile but not Tabrion's transaction modes.
  This is documented (R-T01).
- CI runs the extras combinations and a `core without extras` job.

## Security guarantees

- The core loads no driver or framework that was not asked for; the attack surface of the desktop bundle stays the
  same.
- `errors.classify` recognizes psycopg errors by duck typing, without importing psycopg (specification section 2).

## Verification

- TAB-T-E16: `import tabrion.errors` loads neither psycopg nor SQLAlchemy (`tests/unit/test_import_isolation.py`).
- The `core without extras` job and the meta-path blocker `tests/packaging/block_extras.py`.
- The import-linter contracts "Driver core is stdlib-only", "PostgreSQL helpers need only psycopg" and "Layer B
  builds on layer A, never the reverse".
- QA-5: a clean install per extras combination (`tests/packaging/test_extras_matrix.py`).

## Revision triggers

- SQLAlchemy 2.2 changes event or dialect internals (R-T04; nightly against SQLAlchemy main).
- A consumer needs async (separate ADR; not in 0.x).

## Changes from the plan

- The plan names seven extras combinations; planning documents 05 and 07 list different sets. The extras matrix
  runs the union of both lists, eight combinations: core, `postgres`, `sqlalchemy`, `migrations`, `testing`,
  `postgres,sqlalchemy`, `postgres,migrations,testing`, `all`.
- `tabrion.postgres` imports psycopg only for type checking. At runtime it imports psycopg lazily inside the
  functions, because `Profile` (layer B) holds `SessionSettings` and a SQLite-only application may install
  SQLAlchemy without psycopg. The contract "PostgreSQL helpers need only psycopg" still applies.
