# Diagnosis (`tabrion.check`)

`tabrion.check.check(target)` inspects a connection or a `Database` and returns a list of `Finding`s: settings that
weaken durability, safety or operability. It only reads, never changes anything, and never raises for missing
privileges (a check it cannot run becomes an `info` finding titled "Not checked"). Tabrion Studio uses the same codes.
Contract: [section 7](../api/contract.md#7-tabrioncheck); the codes: [specification section 9](../specification.md#9-diagnosis-tabrioncheck-e).

`target` is a `sqlite3.Connection`, a `psycopg.Connection` or a `tabrion.Database` (`Database.check()` is the same).
`tabrion.check` stays standard library only; it loads the PostgreSQL and layer-B checks by name when it gets such a
target ([ADR 0012](../adr/0012-architecture-checks.md)).

## A `Finding`

```pycon
>>> import sqlite3
>>> from tabrion.check import Severity, check
>>> raw = sqlite3.connect(":memory:")
>>> findings = check(raw)
>>> [(f.code, f.severity.value) for f in findings]  # with the usual SQLite build defaults
[('TAB-SQ-001', 'warning'), ('TAB-SQ-009', 'info')]
>>> first = next(f for f in findings if f.code == "TAB-SQ-001")
>>> first.title
'Foreign keys are off'
>>> first.severity is Severity.WARNING
True
```

Each finding has a stable `code`, a `severity` (`info`, `warning`, `error`), a short English `title`, a `detail`
without secrets or paths, and a `remedy` that says what to do. Sort or gate on `severity`, map to your own texts by
`code`.

A plain `sqlite3.connect` leaves foreign keys off and `trusted_schema` on; the Tabrion profile fixes both:

```pycon
>>> from tabrion import sqlite
>>> profiled = sqlite.connect(":memory:")
>>> check(profiled)
[]
>>> profiled.close()
```

<!-- docs-test: posix -->
```pycon
>>> file_conn = sqlite3.connect("plain.db")  # created 0644 under the usual umask 022, journal mode DELETE
>>> _ = file_conn.execute("CREATE TABLE t (x)")
>>> sorted({"TAB-SQ-001", "TAB-SQ-002", "TAB-SQ-003"} & {f.code for f in check(file_conn)})
['TAB-SQ-001', 'TAB-SQ-002', 'TAB-SQ-003']
>>> file_conn.close()
```

## The codes

| Code | Engine | Finding | Severity |
|---|---|---|---|
| TAB-SQ-001 | SQLite | `foreign_keys` off | warning |
| TAB-SQ-002 | SQLite | `journal_mode` is not WAL (file) | info |
| TAB-SQ-003 | SQLite | file readable by group or others | error |
| TAB-SQ-004 | SQLite | path or side file is a symlink | error |
| TAB-SQ-005 | SQLite | `busy_timeout` below 1000 ms | warning |
| TAB-SQ-006 | SQLite | `quick_check` is not ok | error |
| TAB-SQ-007 | SQLite | SQLite older than 3.37 | warning |
| TAB-SQ-008 | SQLite | WAL file larger than 64 MiB (checkpoint stuck) | warning |
| TAB-SQ-009 | SQLite | `trusted_schema` on | info |
| TAB-PG-001 | PostgreSQL | role is superuser | error |
| TAB-PG-002 | PostgreSQL | role has `BYPASSRLS` | error |
| TAB-PG-003 | PostgreSQL | role owns tables in the schema | warning |
| TAB-PG-004 | PostgreSQL | `statement_timeout` is 0 | warning |
| TAB-PG-005 | PostgreSQL | `lock_timeout` is 0 | info |
| TAB-PG-006 | PostgreSQL | `idle_in_transaction_session_timeout` is 0 | warning |
| TAB-PG-007 | PostgreSQL | `application_name` empty | info |
| TAB-PG-008 | PostgreSQL | server major below 16 | warning |
| TAB-PG-009 | PostgreSQL | role may `CREATE` in the schema | warning |
| TAB-PG-010 | PostgreSQL | remote host without `verify-full` | warning |
| TAB-PG-011 | PostgreSQL | remote host without TLS | error |
| TAB-PG-012 | PostgreSQL | `transaction_timeout` wanted, server older than 17 | info |
| TAB-PG-020 | PostgreSQL | `archive_mode=off` (no point-in-time recovery) | info |
| TAB-PG-021 | PostgreSQL | server is a standby, profile writes | warning |
| TAB-PG-030 | PostgreSQL | extension installed, its schema not in `search_path` | warning |
| TAB-B-001 | layer B | pool without `pre_ping` | info |
| TAB-B-002 | layer B | Alembic schema not current | error |

## The role you expect: `expect`

On PostgreSQL, what is wrong depends on who connects. The default `expect="runtime"` treats owning tables
(TAB-PG-003) and `CREATE` rights (TAB-PG-009) as warnings: the application's runtime role should have neither.
`expect="owner"` drops them, for the role that runs migrations; `expect="any"` reports them as `info`. TAB-PG-001
and TAB-PG-002 are reported whatever you expect. An invalid value raises `ValueError`:

```pycon
>>> check(raw, expect="admin")
Traceback (most recent call last):
  ...
ValueError: ...
>>> raw.close()
```

## Layer B

`Database.check()` adds the layer-B findings to the engine's: TAB-B-001 for a profile without `pool_pre_ping`, and
TAB-PG-012 when the profile asks for a transaction timeout the server cannot apply. `check()` does not know your
Alembic script directory: when a version table exists, TAB-B-002 comes as an `info` finding "Not checked", and
[`require_current`](migrations.md#the-start-up-gate-require_current) is the real gate.

```pycon
>>> import dataclasses
>>> from tabrion import Database, Profile
>>> profile = dataclasses.replace(Profile.default(), pool_pre_ping=False)
>>> with Database.from_url("sqlite:///app.db", profile=profile) as db:
...     [f.code for f in db.check()]
['TAB-B-001']
```

Run `check` in a health endpoint, a `doctor` command or a start-up log line; fail hard only on `error` findings that
you have decided are fatal for your deployment.
