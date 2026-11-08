# Parts baseline: the same application with plain SQLAlchemy

This is reference application A, part 3 (TABRION RA-A-3). It does the same job as `examples/parts`:
- the same schema, migration and catalog functions;
- the same results on SQLite and PostgreSQL.

It uses SQLAlchemy and Alembic alone and never imports `tabrion`; a test checks that. It is the comparison for the
measurement report RA-EVAL (planning document 09 section 7). It is not part of the wheel or the sdist.

## Run it

From a checkout with `pip install -e ".[migrations,postgres]"`, run this in `examples/parts-baseline`. The package
`parts_baseline` is not installed, so from the repository root, set `PYTHONPATH=examples/parts-baseline` instead.

```python
from parts_baseline import create_engine, require_current, upgrade
from parts_baseline.catalog import create_material, create_part

engine = create_engine("sqlite:///parts_baseline.db")  # or "postgresql://user@host/db"
upgrade(engine)  # alembic upgrade head in one write_transaction
require_current(engine)  # raises SchemaNotCurrent unless every migration is applied
create_material(engine, "STEEL", "Steel")
create_part(engine, "P-1", "Bracket", "STEEL")
engine.dispose()
```

The Alembic command line works too:

```console
$ alembic -c examples/parts-baseline/alembic.ini upgrade head
```

## What "plain SQLAlchemy" means here

The baseline follows the SQLAlchemy and Alembic documentation, as the RA-B-4 baseline (`examples/measurements-baseline`)
does, so RA-EVAL compares both use cases on one definition:

- on SQLite, `PRAGMA foreign_keys = ON` in a `connect` event, and the documented pysqlite recipe ("Serializable
  isolation / Savepoints / Transactional DDL" on the SQLAlchemy SQLite page): `isolation_level = None` on connect
  and a `begin` event that emits `BEGIN`, or `BEGIN IMMEDIATE` for a `write_transaction`. Without the recipe pysqlite
  runs DDL and reads outside the transaction: a failing migration is not atomic, and the cycle check of
  `add_component` is not serialized;
- on PostgreSQL, `pool_pre_ping`, and `timezone` and `search_path` through `options`;
- errors told apart by SQLSTATE or by `sqlite3.Error.sqlite_errorname`.

Knowing that recipe is part of what Tabrion saves; RA-EVAL counts it in MW-1 (it is plumbing in `db.py`), not as a
functional deviation.

## What differs from `examples/parts`

Both variants share three pieces of code:
- **`schema.py`, `types.py` and the migration `0001`:** copied from `examples/parts` with the package name changed.
  Tabrion has no column types, so both variants need `UtcDateTime` and `ExactDecimal`.
- **`errors.py`:** copied unchanged.
- **`catalog.py`:** the same functions with an `Engine` in place of a `Database`. It uses `engine.connect()` in
  place of `read()` and `db.write_transaction` in place of `transaction()`. It catches `sqlalchemy.exc.DBAPIError`
  in place of Tabrion's `DatabaseError`. On PostgreSQL it takes the same advisory transaction lock before the cycle
  check.

The plumbing that Tabrion provides in `examples/parts` lives in `parts_baseline/db.py` here:

| Concern | `examples/parts` (Tabrion) | `examples/parts-baseline` |
|---|---|---|
| Opening the database | `Database.from_url(url, profile=...)` | `create_engine`: the `connect` and `begin` events on SQLite; `pool_pre_ping`, the psycopg 3 driver name, and `timezone` and `search_path` through `options` on PostgreSQL |
| Transactions | `transaction()` (`BEGIN IMMEDIATE` on SQLite) and `read()` (read-only) | `write_transaction` (`BEGIN IMMEDIATE` on SQLite) and `engine.connect()` (`BEGIN`); not read-only |
| Errors | `DatabaseError.kind` (`UNIQUE`, `FOREIGN_KEY`, `CHECK`, `DATA`) and the constraint's name | `classify`: SQLSTATE on PostgreSQL, `sqlite_errorname` on SQLite; no constraint name |
| The advisory lock key of `add_component` on PostgreSQL | `tabrion.postgres.lock_key` | `relations_lock`, a SHA-256 of the name |
| Start-up gate | `tabrion.migrations.status` / `require_current` | `status` / `require_current` written with Alembic's `MigrationContext` and `ScriptDirectory` |

## Measurements for RA-EVAL

The same suite runs against both variants:
- `tests/examples/parts_suite.py` holds the tests;
- `test_parts.py` runs them against `examples/parts`;
- `test_parts_baseline.py` runs them against this baseline.

The suite covers:
- migrations and the start-up gate, the reflected schema, a failing upgrade, concurrent SQLite writers for
  `upgrade`, `downgrade` and the command line;
- the shared domain code: exact round trips, every `CHECK`, the relation rules, the stored decimal representation,
  the refusal of floats and naive timestamps, decimal and timestamp ordering;
- CRUD, the life cycle, the relations and the bill of materials, the lost-update test and the same test without the
  version check;
- the review cases of #29: concurrent cycle additions, a stored cycle, a 1500-level chain, 22 diamond levels, exact
  rounding, a total below the scale, the shortest depth, `updated_at`, the `NotFound` paths, `InvalidValue`, a
  concurrent first `set_property`.

Every test runs on SQLite and PostgreSQL. A test the baseline fails is listed in `BASELINE.deviations` with its
reason. It runs as a strict `xfail`: the suite stays green, and it turns red if the deviation disappears.

| Result | Tabrion | Baseline |
|---|---|---|
| Suite, SQLite and PostgreSQL 16 | all pass | all pass but one test (8 cases per engine) |
| **Deviation:** a violated `CHECK` names its constraint (`test_violations_name_their_constraint`) | yes (`classification.constraint`) | no: `classify` gives the kind only |
| MW-3, lost update (5 threads, SQLite and PostgreSQL) | 5, no lock error | 5, no lock error: the optimistic lock is in the shared `catalog.py` |
| A failing upgrade; concurrent cycle additions; migrations under a concurrent SQLite writer | atomic; serialized; wait | the same, through the pysqlite recipe and `BEGIN IMMEDIATE` |
| MW-1 inputs (see below) | `db.py` 28 + `env.py` 17 = 45 | `db.py` 107 + `env.py` 22 = 129 |
| MW-8, changing the engine | the URL only | the URL only |

**How MW-1 is counted.** The files are each variant's database plumbing: `db.py` and `migrations/env.py`. The rule
matches `cloc` for Python: no blank lines, no comments and no docstrings. The error mapping in `catalog.py` (`_write`
and its table) has the same size in both variants and is left out of both.

The "Tests-Setup" category of planning document 09 section 7 is not in these numbers. Both variants' PostgreSQL
tests use `tabrion.testing`'s `tabrion_postgres_schema` and `tabrion_postgres_url` (the `target` fixture of
`parts_suite.py`). The baseline borrows them, so its count leaves out the schema-per-test fixture a plain project
would write itself. This understates Tabrion's saving; RA-EVAL should add that fixture to the baseline side.

The suite leaves out only the read-only profile and the PostgreSQL session time zone, which stay in `test_parts.py`
because the baseline has no counterpart.

On SQLite a violated `RESTRICT` foreign key reports `SQLITE_CONSTRAINT_TRIGGER`, which neither variant classifies
as a foreign key (TODO TB-110). Both variants declare `NO ACTION`, so they run the same DDL.
