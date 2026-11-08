# Parts: a portable schema with Alembic

Reference application A, part 1 (TABRION RA-A-1). This is a parts catalog that runs the same way on SQLite and on
PostgreSQL. It uses layer B (`tabrion.Database`) and `tabrion.migrations`; there is one schema and one set of Alembic
migrations, and `require_current` is the start-up gate. `parts.catalog` (RA-A-2) adds the functions: CRUD, status
changes with an optimistic lock and relation queries.

## Run it

From a checkout with `pip install -e ".[migrations,postgres]"`, in `examples/parts` (the package `parts` is not
installed; from the repository root, set `PYTHONPATH=examples/parts` instead):

```python
from parts import open_database, require_current, upgrade

database = open_database("sqlite:///parts.db")  # or "postgresql://user@host/db"
upgrade(database)  # alembic upgrade head, in one database.transaction()
require_current(database)  # raises SchemaNotCurrent unless every migration is applied
database.close()
```

The Alembic command line works too; it takes the URL from `sqlalchemy.url` in `alembic.ini`:

```console
$ alembic -c examples/parts/alembic.ini upgrade head
```

## The catalog functions (RA-A-2)

`parts.catalog` takes the application's `Database`; each function is one Tabrion transaction (`read()` for queries,
`transaction()` for changes), and the same code runs on both engines:

| Function | What it does |
|---|---|
| `create_material`, `delete_material` | a material; deleting one in use raises `InUse` |
| `create_part`, `get_part`, `list_parts(status=...)` | a part starts as `draft` with version 1; `create_part` returns the row its own transaction stored |
| `rename_part`, `change_status`, `delete_part` | each needs `expected_version`, the version the caller read |
| `set_property`, `properties` | exact `Decimal` values with their unit; two concurrent first inserts of a key end as one insert and one update |
| `add_component`, `components`, `bill_of_materials`, `where_used` | the relations; a cycle raises `CycleRefused` |

**Optimistic lock.** A change runs `UPDATE part SET ..., version = version + 1 WHERE part_number = ? AND
version = ?`. When no row changes, another writer was first and the call raises `StaleVersion`: read again and
retry. Nothing is held between the read and the write, so there is no lost update.

**Life cycle** (`catalog.TRANSITIONS`): `draft` goes to `released` or `obsolete`; `released` to `blocked` or
`obsolete`; `blocked` back to `released` or to `obsolete`; `obsolete` is final. Any other change raises
`InvalidTransition`. `change_status` checks the version before the transition, so a stale caller gets
`StaleVersion` (read again) rather than `InvalidTransition`.

**No cycle, also under concurrency.** `add_component` checks that the component does not contain the assembly, then
inserts. Two such additions with disjoint rows (`A → B` and `C → D` while `B → C` and `D → A` exist) could each pass
the check and together close a cycle. SQLite runs one writer at a time (`BEGIN IMMEDIATE`); on PostgreSQL each
`add_component` first takes the transaction-level advisory lock `catalog.RELATIONS_LOCK`
(`pg_catalog.pg_advisory_xact_lock(tabrion.postgres.lock_key("parts.part_relation"))`), released by the commit or
rollback. Advisory locks belong to the database, not the schema, so several catalogs (schemas) in one database share
the lock: that costs throughput, not correctness.
That serializes exactly the relation additions, with no retry loop; `SERIALIZABLE` would be correct as well, but
the caller would have to retry serialization failures, also ones from unrelated reads. Relations written past
`add_component` with a cycle make `bill_of_materials` raise `CycleFound`.

**Errors** are the application's own (`parts.errors`), with fixed texts. A Tabrion `IntegrityViolation` becomes one
of them by its `Kind` (`UNIQUE` → `AlreadyExists`, `FOREIGN_KEY` → `InUse`, and always `CHECK` and `DATA`, such as an
empty name, a text longer than its column or a quantity that is not positive, → `InvalidValue`), raised outside the
`except` block, so no driver message (which may quote a value) is chained. A quantity or property value that does not
fit its column (too large, too many decimals, `NaN`, a `float`) is `InvalidValue` too, checked before any SQL.

**Bill of materials.** One recursive query (`WITH RECURSIVE`, the same SQL on both engines, one statement and so one
snapshot) returns the relations below the assembly with the part numbers. Python then visits the parts in
topological order (Kahn's algorithm): linear in the relations, with no recursion depth limit, and a cycle found on
the way raises `CycleFound`. Quantities are multiplied along each path and summed over the paths exactly (a
`decimal` context that never rounds); each total is then rounded once, half even, to `catalog.BOM_SCALE` (6)
decimal places. A positive total that would round to 0 there (three levels of `0.001`, for example) raises
`InvalidValue` instead of listing the part as needed 0 times. The depth is the shortest path.

## The schema

| Table | Columns | Rules |
|---|---|---|
| `material` | `id`, `code`, `name`, `created_at` | `code` unique |
| `part` | `id`, `part_number`, `name`, `material_id`, `status`, `version`, `created_at`, `updated_at` | `part_number` unique; `status` one of `draft`, `released`, `blocked`, `obsolete` (default `draft`); `version` starts at 1, for the optimistic lock of RA-A-2; a material in use cannot be deleted (`NO ACTION`, see below) |
| `property` | `id`, `part_id`, `key`, `value`, `unit` | one value per part and key; deleted with its part |
| `part_relation` | `assembly_id`, `component_id`, `quantity` | an assembly and its components; no part in itself; `quantity > 0`; a part used as a component cannot be deleted |

The portable mappings follow specification section 6 and the cases TAB-T-D01..D09 of `tabrion.testing.profiles`:

| Kind | Python | SQLite | PostgreSQL | Where |
|---|---|---|---|---|
| IDs | `int` | `INTEGER PRIMARY KEY` (the `rowid` alias) | `bigint GENERATED ALWAYS AS IDENTITY` | `schema.ID` |
| Timestamps | aware `datetime` in UTC (a naive one is refused) | fixed-width ISO 8601 text `YYYY-MM-DDTHH:MM:SS.ffffffZ` | `timestamptz` | `types.UtcDateTime` |
| Decimals | `Decimal` (a `float` is refused, and so is a value with more digits than `numeric(p, s)` holds) | the scaled integer `value * 10**s` ("cents", `p` at most 18) | `numeric(p, s)` | `types.ExactDecimal` |
| Text | `str` | `VARCHAR` plus a `CHECK` on `length()` | `varchar(n)` plus the same `CHECK` | `schema.py` |
| References | | foreign keys, enforced (`foreign_keys=ON` in the profile) | foreign keys | `schema.py` |

Every constraint (the primary keys `pk_<table>` included) and index has a name of its own, so both engines carry the
same names. Decimals and timestamps order and compare alike on both engines: `ORDER BY`, `<` against a bound
parameter, `min`, `max`, uniqueness, `sum` of one decimal column while the total fits `numeric(p, s)` (otherwise sum
in Python), and `+`/`-` with a bound `Decimal`. Do other arithmetic on decimals in Python, not in SQL: on SQLite,
`avg`, multiplying or dividing (also by a constant), `round`, `abs` and `CAST` are silently off by a factor of
`10**s` or truncated, with no error. Compare with bound parameters, not SQL literals (`value > :limit`, never `value > 5`), and do not compare timestamps with SQLite's `CURRENT_TIMESTAMP`
(`YYYY-MM-DD HH:MM:SS`).

References that must block a delete use the default `NO ACTION`, not `RESTRICT`: SQLite reports a `RESTRICT`
violation as `SQLITE_CONSTRAINT_TRIGGER`, which Tabrion does not classify as a foreign key yet (TODO TB-110).

`upgrade` and `downgrade` run Alembic inside `database.transaction()`, as does the command line through `env.py`:
one transaction for all revisions, `BEGIN IMMEDIATE` on SQLite (two instances starting at once, or a running writer,
are waited for, not a `database is locked` failure) and Tabrion's errors. A test pins this for `upgrade`,
`downgrade` and the command line.

## Tests

`tests/examples/test_parts.py` runs every test on SQLite and on PostgreSQL; most of them are the shared suite
`tests/examples/parts_suite.py`, which also runs against the plain SQLAlchemy baseline `examples/parts-baseline`
(RA-A-3, with its measured deviations). The PostgreSQL case needs
`TABRION_TEST_POSTGRES` and runs in a schema of its own from `tabrion.testing`'s `tabrion_postgres_schema`. The tests
cover:

- a fresh database is not current (`pending` is `0001`), and after `upgrade` `require_current` passes, also under a
  read-only profile;
- **identical on both engines:** the reflected tables, columns, nullability, keys, foreign keys, unique and check
  constraints and indexes equal what `parts.schema` declares, and Alembic's own `compare_metadata`, with types
  compared, finds no difference;
- `downgrade base` (then `require_current` raises again) and a second `upgrade`; a failing second revision leaves
  no table, no version row and an idle connection; `upgrade` waits for a concurrent SQLite writer;
- exact round trips (`Decimal`, UTC timestamps also before the year 1000 and under another PostgreSQL session time
  zone, the server defaults) and every `CHECK` constraint, the relation rules and the foreign key on both engines;
- decimals order, compare and aggregate the same way on both engines, stored as scaled integers on SQLite;
- the `alembic.ini` command line path through `env.py`;
- RA-A-2: CRUD, the life cycle, stale versions, exact properties, the relations and the bill of materials, and the
  **lost-update test**: five threads read the same version and then write; with the optimistic lock four get
  `StaleVersion` and retry, so the counter ends at 5 (version 6) with no lock error. Without the version check the
  same five writes end at 1;
- the review of #29: two concurrent additions cannot close a cycle (a barrier makes both check first; it fails on
  PostgreSQL without the advisory lock), a stored cycle gives `CycleFound`, a 1500-level chain and 22 diamond
  levels (2**22 paths) are linear, exact products are rounded once, the shortest depth wins, `updated_at` moves,
  every unknown part or material is `NotFound`, `CHECK` and length violations are `InvalidValue` without a chained
  driver error, and two concurrent first `set_property` calls of one key both succeed.

## Measurement notes for RA-EVAL (planning document 09 section 7)

- **MW-1:** the database plumbing is `parts/db.py`, `parts/types.py` and `parts/migrations/env.py`; the schema
  (`schema.py`, the migration) is domain code in both variants.
- **MW-8:** the application code switches engines by its URL only (`open_database("sqlite:///...")` or
  `open_database("postgresql://...")`); the tests run unchanged on both.
- **MW-3:** `test_lost_update_is_impossible` (5 / 0 on both engines); `test_lost_update_without_the_version_check`
  shows what the baseline must prevent itself.
- MW-1 also counts `_write` in `parts/catalog.py` (the error mapping); the rest of `catalog.py` is domain code.
- MW-2 and MW-4 follow with RA-A-3 and RA-EVAL; MW-5 is measured from this README.
