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
upgrade(engine)  # alembic upgrade head in one engine.begin()
require_current(engine)  # raises SchemaNotCurrent unless every migration is applied
create_material(engine, "STEEL", "Steel")
create_part(engine, "P-1", "Bracket", "STEEL")
engine.dispose()
```

The Alembic command line works too:

```console
$ alembic -c examples/parts-baseline/alembic.ini upgrade head
```

## What differs from `examples/parts`

Both variants share three pieces of code:
- **`schema.py`, `types.py` and the migration `0001`:** copied from `examples/parts` with the package name changed.
  Tabrion has no column types, so both variants need `UtcDateTime` and `ExactDecimal`.
- **`errors.py`:** copied unchanged.
- **`catalog.py`:** the same functions with an `Engine` in place of a `Database`. It uses `engine.connect()` in
  place of `read()` and `engine.begin()` in place of `transaction()`. It catches `sqlalchemy.exc.IntegrityError`
  (`DBAPIError`) in place of Tabrion's `DatabaseError`. On PostgreSQL it takes the same advisory transaction lock
  before the cycle check; on SQLite it adds nothing (see the second deviation).

The plumbing that Tabrion provides in `examples/parts` lives in `parts_baseline/db.py` here:

| Concern | `examples/parts` (Tabrion) | `examples/parts-baseline` |
|---|---|---|
| Opening the database | `Database.from_url(url, profile=...)` | `create_engine`: a `connect` listener for `PRAGMA foreign_keys=ON`, `pool_pre_ping`, the psycopg 3 driver name, and `timezone` and `search_path` through `options` |
| Transactions | `transaction()` (`BEGIN IMMEDIATE` on SQLite) and `read()` (read-only) | `engine.begin()` and `engine.connect()`; pysqlite's default transaction handling |
| Errors | `DatabaseError.kind` (`UNIQUE`, `FOREIGN_KEY`, `CHECK`, `DATA`) | `classify`: SQLSTATE on PostgreSQL, the message text on SQLite |
| The advisory lock key of `add_component` on PostgreSQL | `tabrion.postgres.lock_key` | `relations_lock`, a SHA-256 of the name |
| Start-up gate | `tabrion.migrations.status` / `require_current` | `status` / `require_current` written with Alembic's `MigrationContext` and `ScriptDirectory` |

## Measurements for RA-EVAL

The same suite runs against both variants:
- `tests/examples/parts_suite.py` holds the tests;
- `test_parts.py` runs them against `examples/parts`;
- `test_parts_baseline.py` runs them against this baseline.

The suite covers:
- migrations and the start-up gate;
- the reflected schema;
- a failing upgrade;
- a concurrent SQLite writer;
- decimal and timestamp ordering;
- CRUD, the life cycle, the relations and the bill of materials;
- the review cases of #29: concurrent cycle additions, a stored cycle, a 1500-level chain, 22 diamond levels, exact
  rounding, the shortest depth, `updated_at`, the `NotFound` paths, `InvalidValue`, a concurrent first
  `set_property`;
- the lost-update test, and the same test without the version check.

Every test runs on SQLite and PostgreSQL. A test the baseline fails is listed in `BASELINE.deviations` with its
reason. It runs as a strict `xfail`: the suite stays green, and it turns red if the deviation disappears.

| Result | Tabrion | Baseline |
|---|---|---|
| Suite, SQLite and PostgreSQL 16 | all pass | all pass but two, both on SQLite |
| **Deviation:** a failing second revision during `upgrade` from base, on SQLite | nothing is left behind | the tables of `0001` stay, with an empty `alembic_version` table; the next `upgrade` fails because the tables exist. pysqlite opens a transaction only before `INSERT`, `UPDATE` and `DELETE`, so Alembic's DDL runs outside the `engine.begin()` transaction (PostgreSQL is atomic in both variants) |
| **Deviation:** two concurrent `add_component` calls with disjoint rows (`A → B`, `C → D` while `B → C` and `D → A` exist), on SQLite | the second one is refused (`BEGIN IMMEDIATE` serializes the check) | both succeed and close a cycle: pysqlite runs the check outside a transaction and takes the write lock only with the `INSERT`. PostgreSQL is correct in both variants (the advisory lock) |
| MW-3, lost update (5 threads, SQLite and PostgreSQL) | 5, no lock error | 5, no lock error: the optimistic lock is in the shared `catalog.py` |
| `upgrade` while another SQLite writer holds the database | waits (`BEGIN IMMEDIATE`) | waits too: pysqlite reads outside a transaction, so there is no stale snapshot to fail on |
| MW-1 inputs: code lines of the database plumbing (no blank lines, comments or docstrings) | `db.py` 28 + `env.py` 17 = 45 | `db.py` 98 + `env.py` 22 = 120 |
| MW-8, changing the engine | the URL only | the URL only |

The error mapping in `catalog.py` (`_write` and its table) has the same size in both variants. The suite does not cover:
- the read-only profile;
- Tabrion's classified errors for statement and type failures;
- the PostgreSQL session settings.

Those tests stay in `test_parts.py`, because the baseline has no counterpart.

On SQLite, the baseline's `classify` reads the message text, so it would also map a `RESTRICT` violation to a
foreign key. Tabrion maps that one to unknown (TODO TB-110). Both variants declare `NO ACTION`, so they run the same
DDL.
