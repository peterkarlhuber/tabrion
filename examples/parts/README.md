# Parts: a portable schema with Alembic

Reference application A, part 1 (TABRION RA-A-1). This is a parts catalog that runs the same way on SQLite and on
PostgreSQL. It uses layer B (`tabrion.Database`) and `tabrion.migrations`; there is one schema and one set of Alembic
migrations, and `require_current` is the start-up gate. RA-A-2 adds the functions (CRUD, status changes with an
optimistic lock, relation queries).

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

## The schema

| Table | Columns | Rules |
|---|---|---|
| `material` | `id`, `code`, `name`, `created_at` | `code` unique |
| `part` | `id`, `part_number`, `name`, `material_id`, `status`, `version`, `created_at`, `updated_at` | `part_number` unique; `status` one of `draft`, `released`, `blocked`, `obsolete` (default `draft`); `version` starts at 1, for the optimistic lock of RA-A-2; a material in use cannot be deleted |
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

`upgrade` and `downgrade` run Alembic inside `database.transaction()`, as does the command line through `env.py`:
one transaction for all revisions, `BEGIN IMMEDIATE` on SQLite (two instances starting at once, or a running writer,
are waited for, not a `database is locked` failure) and Tabrion's errors. A test pins this for `upgrade`,
`downgrade` and the command line.

## Tests

`tests/examples/test_parts.py` runs every test on SQLite and on PostgreSQL. The PostgreSQL case needs
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
- the `alembic.ini` command line path through `env.py`.

## Measurement notes for RA-EVAL (planning document 09 section 7)

- **MW-1:** the database plumbing is `parts/db.py`, `parts/types.py` and `parts/migrations/env.py`; the schema
  (`schema.py`, the migration) is domain code in both variants.
- **MW-8:** the application code switches engines by its URL only (`open_database("sqlite:///...")` or
  `open_database("postgresql://...")`); the tests run unchanged on both.
- MW-2, MW-3 and MW-4 follow with the functions of RA-A-2; MW-5 is measured from this README.
