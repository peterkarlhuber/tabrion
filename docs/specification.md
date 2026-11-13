# Tabrion specification

Product and technical specification of Tabrion (work package TAB-001). English version of planning document 02
("Tabrion: product and technical specification"), chapters 1 to 11, with the API stability policy (its chapter 14)
as chapter 12. The exact signatures are in the [API contract](api/contract.md); this document defines purpose,
structure, profiles and rules. Where the two disagree, the API contract wins and the difference is a defect of this
page.

Markers: **[F]** proven in code or by a probe · **[E]** decision of the plan · **[A]** assumption.

Status: milestone M1. The API exists as typed stubs (G-T1); the behavior described here is implemented in M2.

---

## 1. Product vision and scope

### 1.1 Vision

Tabrion is a **small, secure, model-neutral Python library** for SQLite and PostgreSQL. It provides the database
foundations that every Levuvo application builds anew today, each to a different standard:

- safe connections and files,
- correct transactions,
- value-free errors,
- role checks,
- diagnosis,
- test fixtures.

It is not a new abstraction over SQL and not a replacement for SQLAlchemy or psycopg.

**Guiding principle:** Tabrion makes the safe setting the default and keeps the established tools visible.

### 1.2 Users

| User | Dependency (edge list K-33) | Layer / modules | Purpose |
|---|---|---|---|
| **Axentra** | **required** `tabrion[postgres]` (K-30) | A: `tabrion.postgres` (session configuration of its own pool, ping, roles, grants, advisory locks), `tabrion.errors`, `tabrion.sql`; dev: `tabrion[testing]` | generic database functions are maintained once in Tabrion; RLS, `DbScope` and pool ownership stay in Axentra |
| **Nodestro core** (Desktop, Local Docker) | required `tabrion` (stdlib only), per migration plan (K-32) | A: `tabrion.sqlite`, `tabrion.errors`, `tabrion.config` | `SQLiteStore` connect, transaction, migration, backup, diagnosis |
| **Nodestro server profile** | `tabrion[postgres]` in the extra `nodestro-runtime[postgres]` | A: `tabrion.postgres`, `tabrion.errors`; dev: `tabrion[testing]` | `PostgresDatabase` helpers, test fixtures |
| **Orkexa** | optional `orkexa[postgres]` → `tabrion[postgres]` (K-31); core → `tabrion` (**E-ORK-CORE = yes**, decided 2026-11-07, MIG-ORK-3) | A: `tabrion.postgres` (`PsycopgSource`: timeouts, ping, errors); possibly `tabrion.sqlite`; dev: `tabrion[testing]` | a lean core stays the yardstick |
| **New applications** (parts, measurements ...) | as needed | B (SQLAlchemy), A where useful | reference applications RA-A, RA-B |
| **Tabrion Studio** | required `tabrion[postgres,sqlalchemy,migrations]` | A + B, public API only | explorer, editor, diagnosis |

Tabrion itself depends on **no** Levuvo platform (P6). Edges are allowed only towards Tabrion; cycles are forbidden
(P5). See [ADR 0008](adr/0008-dependency-principles-and-edges.md).

### 1.3 Non-goals (binding)

| Non-goal | Reason |
|---|---|
| ORM, session, identity map, model classes | SQLAlchemy ORM exists; Tabrion stays model-neutral |
| Own query language or builder | SQLAlchemy Core is the builder |
| Own connection pool | psycopg_pool and the SQLAlchemy pool suffice; the shared Nodestro/Axentra pool (M10) stays unchanged |
| PostgreSQL migration runner in 0.x | Alembic for new applications; the existing runners stay separate (G-EM = no) |
| Tenant, user or RLS model | belongs to Axentra or the products; Tabrion only checks whether RLS *can apply* to a role |
| `pg_dump`/`pg_restore` wrappers, PostgreSQL backup orchestration | an operations tool (kit documentation), not a library |
| Async API | no existing user needs it; can be added from 1.x without a break (layer A is synchronous, SQLAlchemy has `AsyncEngine`) |
| More engines (MySQL, DuckDB ...) | no need; the profile tables are built so that an engine would be a new module, not an adapter framework |
| Upsert/merge helpers, a portable type library | see chapters 6 and 7 |
| UI logic, connection-profile storage, keyring | belongs in Tabrion Studio |
| AI, vector, RAG functions | Orkexa; Tabrion only reports the technical extension status (for example pgvector) |

---

## 2. Module structure

```
tabrion/                     package; layer B in the package root (importable only with the extra [sqlalchemy])
├── __init__.py              lazy exports (PEP 562): Database, Profile, Transaction, TabrionError, Result, Column ...
├── _version.py
├── errors.py                layer A, stdlib: Kind, Classification, classify, safe_message, DbErrorRedactionFilter, hierarchy
├── sql.py                   layer A, stdlib: split_statements
├── config.py                layer A, stdlib: safe_url, url_from_env (including *_FILE)
├── sqlite.py                layer A, stdlib: SQLiteProfile, connect, apply_profile, check_path, write_/read_transaction,
│                            migrate, backup, integrity_check, quick_check, foreign_key_check, diagnose
├── postgres/                layer A, extra [postgres] (psycopg)
│   ├── __init__.py          SessionSettings, configure, ping, diagnose, extension_status
│   ├── roles.py             RoleInfo, RoleReport, role_info, check_role, inspect_role, grant_runtime_role
│   ├── locks.py             SessionLock, lock_key, advisory helpers
│   └── _check.py            private: the TAB-PG-* checks of tabrion.check
├── check.py                 Finding, Severity, check(...); SQLite checks itself, loads the collectors by name
├── _sa/                     layer B, extra [sqlalchemy]; private, public through tabrion.*
│   ├── database.py          Database, Transaction, Health
│   ├── profile.py           Profile, TLS rules
│   ├── events.py            connect/begin listeners (call tabrion.sqlite/postgres)
│   ├── result.py            Result, Column (exported since 0.2)
│   ├── _check.py            the layer-B checks of tabrion.check
│   ├── _types.py            the documentation-only aliases Statement, Params, Isolation
│   └── _v02.py              0.2 methods of Database and Transaction (mixins the public classes inherit since 0.2)
├── migrations.py            layer B, extra [migrations]: status, require_current, SchemaStatus (without env.py)
└── testing/                 extra [testing] (pytest); pytest plugin only on explicit request (pytest_plugins)
    ├── __init__.py          configure(), fixtures
    ├── postgres.py          schema per test, database per session, runtime-role mode, require_extension
    ├── sqlite.py            sqlite_path, sqlite_connection
    └── profiles.py          profile and portability test cases for new applications
```

**Import rules [E]** (enforced by import-linter in `pyproject.toml` and by blocked-module tests):

| Module | may import | must not import |
|---|---|---|
| `errors`, `sql`, `config`, `sqlite`, `check` | stdlib | psycopg, sqlalchemy, alembic, pytest |
| `postgres.*` | stdlib, psycopg | sqlalchemy, alembic, pytest, psycopg_pool |
| `_sa.*` | layer A, sqlalchemy | pytest, alembic |
| `migrations` | `_sa`, alembic | pytest |
| `testing.*` | everything above, pytest | — |
| everything | — | orkexa, axentra, axentra_studio, nodestro_*, tabrion_studio (P6, K-33) |

`errors.classify` recognises psycopg errors by **duck typing** (`sqlstate`, `diag`) instead of importing psycopg,
so that `tabrion.errors` stays standard-library only.

**Layer B without the extra:** `import tabrion` always works. Accessing `tabrion.Database` without SQLAlchemy
raises `ImportError` with the hint `pip install "tabrion[sqlalchemy]"`.

---

## 3. How the layers work together

### 3.1 Principle

Layer B is a **thin wrapper around a SQLAlchemy `Engine`**. It applies layer-A functions through SQLAlchemy events
to the same DBAPI connection. There is no second connection, no own pool and no adapter between the layers. [E,
proven by probe s.py: a `begin` event with `set_config(..., true)` takes effect in the transaction and is gone
afterwards]

| SQLAlchemy event | Layer-A call | SQLite | PostgreSQL |
|---|---|---|---|
| `connect` (new DBAPI connection) | `tabrion.sqlite.apply_profile` / `tabrion.postgres.configure` | `isolation_level=None` (driver autobegin off), pragmas | session settings in one round trip, then `commit()`; TLS is already enforced when connecting |
| `begin` (transaction start) | mode from the execution option `tabrion_mode` | `write` → `BEGIN IMMEDIATE`; `read` → `PRAGMA query_only=ON` + `BEGIN` | `read` → SQLAlchemy option `postgresql_readonly=True` (dialect feature, not rebuilt) |
| `reset`/`checkin` | reset | `PRAGMA query_only=OFF`; if that fails, the connection is invalidated | — (end of transaction = end of READ ONLY) |

**Consequences:**

- Code that uses `db.engine.connect()` directly gets the connection profile (pragmas, timeouts) but **not** the
  Tabrion transaction modes. SQLite write transactions outside `db.transaction()` then run as `BEGIN DEFERRED`.
  This is documented; `tabrion.check` does not report it (it cannot be detected).
- Alembic migrations through `db.engine` run under the profile. For SQLite the documentation recommends
  `render_as_batch=True`.

### 3.2 Transaction model (both layers)

| Rule | SQLite | PostgreSQL |
|---|---|---|
| Write transaction | `BEGIN IMMEDIATE`: write lock at once, the busy handler applies. Proven by q.py/q2.py: deferred loses 4 of 5 updates or gives 4 × "database is locked"; IMMEDIATE gives 5 of 5 | `BEGIN` (READ COMMITTED by default); isolation selectable (`repeatable_read`, `serializable`) |
| Read transaction | deferred `BEGIN` + `query_only=ON` → a write attempt gives `READ_ONLY` | `BEGIN READ ONLY` → a write attempt gives SQLSTATE 25006 (`READ_ONLY`) |
| Nesting | layer A: `UsageError` when a transaction is already open (a consumer such as Nodestro keeps its own reentrancy counter). Layer B: `transaction()` inside `transaction()` in the same thread → `UsageError`; savepoints explicitly through `tx.savepoint()` (experimental) | same |
| Autonomous single statement (`db.execute`) | its own `BEGIN IMMEDIATE ... COMMIT` | its own transaction |
| Commit outcome unknown | a lost connection during `COMMIT`, or after an autocommit write was sent, gives `Classification.outcome_unknown=True`. **Tabrion never retries automatically** (lesson of finding F-02) | same |

### 3.3 Threading and resources

| Object | Thread safety | Lifetime |
|---|---|---|
| `sqlite3.Connection` from `tabrion.sqlite.connect` | `check_same_thread=False`; the caller serialises use (like Nodestro's `RLock`, Orkexa's store lock) | the caller closes it |
| psycopg connection | one connection = one thread at a time (psycopg rule) | the caller |
| `SessionLock` | thread-safe (internal lock), own unpooled connection | `close()`/context manager; locks end with the connection |
| `Database` | thread-safe (engine/pool) | `close()` → `engine.dispose()`; context manager |
| `Transaction` | bound to the opening thread; only `cancel()` may come from another thread | end of the `with` block |
| `Result` | immutable, fully materialised (up to `max_rows`) | any |

**Resource rules:**

- Every layer-B method returns the connection in `finally`.
- Layer-A functions open only what they close themselves. The exceptions are `connect()` and `SessionLock`; both
  are documented.
- A `ResourceWarning` is emitted when an unclosed `Database` is garbage-collected (test TAB-T-B19).

---

## 4. Profiles

### 4.1 SQLite profile (`SQLiteProfile`, defaults) [E]

| Setting | Default | Reason | Overridable | Today |
|---|---|---|---|---|
| Permissions of a new file | `0o600`, created with `os.open(O_CREAT\|O_EXCL\|O_NOFOLLOW)` **before** `sqlite3.connect` | probe p.py: SQLite creates 0644; WAL/SHM inherit the main file's mode | `file_mode` | Orkexa yes; Nodestro **no** |
| Symlink refusal | main file and `-wal`, `-shm`, `-journal` | prevent redirection of private files | no | Orkexa yes; Nodestro no |
| `journal_mode` | `WAL` (files), unchanged for `:memory:` | readers do not block writers | yes (`delete`, e.g. network drives) | both WAL |
| `busy_timeout` | 30 000 ms (through `sqlite3.connect(timeout=30)`) | parity with the existing platforms; reliable only with `BEGIN IMMEDIATE` | yes | both 30 s |
| `foreign_keys` | `ON` | probe p.py: the default is OFF; F-01: no existing foreign keys, so neutral | yes | neither |
| `synchronous` | `FULL` | durability before throughput; `NORMAL` is consistent with WAL but can lose the last transaction on power loss | yes (`NORMAL` for bulk measurements, documented) | both implicitly FULL |
| `secure_delete` | `ON` | deleted data is overwritten (privacy) | yes | Orkexa ON, Nodestro OFF (Nodestro passes `secure_delete=False` = today's behavior) |
| `trusted_schema` | `OFF` | stops schema objects (views, triggers) from calling unsafe SQL functions | yes | neither |
| `cell_size_check` | `OFF` (SQLite default) | cost; integrity is checked explicitly | yes | — |
| Extension loading | off; `enable_load_extension(True)` only inside the context manager `loading_extensions()`, off again afterwards | Orkexa's sqlite-vec pattern | yes | Orkexa |
| Autocommit mode | `isolation_level=None` (no implicit driver transaction) | explicit `BEGIN` control | no | both |
| `check_same_thread` | `False` | existing pattern; the caller serialises | yes | both |
| Read-only open | `connect(..., read_only=True)`: URI `mode=ro` + `query_only=ON`, creates no file | Studio, diagnosis | — | — |

### 4.2 PostgreSQL session profile (`SessionSettings`) [E]

| Setting | Default for new applications | Reason | `None` = do not set | Today |
|---|---|---|---|---|
| `statement_timeout` | 30 000 ms | probe r.py: default 0 (unlimited) | yes | Nodestro 30 s, Axentra 30 s |
| `lock_timeout` | 10 000 ms | bound the wait for locks; no platform sets it yet | yes | neither |
| `idle_in_transaction_session_timeout` | 300 000 ms | end hanging transactions | yes | Nodestro 300 s, Axentra no |
| `application_name` | required in layer B (`Profile.application_name`, default `"tabrion"`) | attribution in `pg_stat_activity` | yes | neither |
| `search_path` | `None` (unchanged) | the schema is the application's choice; set quoted (like Nodestro) | yes | Nodestro optional |
| `transaction_timeout` | `None` (effective on PostgreSQL 17 or later only) | bounds the total duration of a transaction; skipped on PostgreSQL 16, finding TAB-PG-012 | yes | neither |
| `TimeZone` | `UTC` | `timestamptz` renders in UTC | yes | neither |
| `connect_timeout` | 10 s (a connection parameter, not a session setting) | parity with Nodestro | yes | Nodestro 10 s |

**Rules for `configure()` [E]:**

- A single `SELECT set_config(...)` round trip with `is_local=false`.
- `configure()` itself **never commits** (rule T-NOTX). Without autocommit the `SELECT` opens an implicit
  transaction that its caller commits, as Axentra's `_configure` does today; otherwise a later rollback would
  discard the settings. `session_configurator(settings)`, the pool callback, commits exactly when the connection was
  IDLE without autocommit before the call (API contract section 4.1).

### 4.3 Layer-B profile (`Profile`) [E]

| Field | Default | Note |
|---|---|---|
| `sqlite` | `SQLiteProfile()` | see 4.1 |
| `postgres` | `SessionSettings()` | see 4.2 |
| `application_name` | `"tabrion"` | copied into `SessionSettings` |
| `pool_size` / `max_overflow` | 5 / 0 | a fixed upper bound (probe r.py: SQLAlchemy default 5 + 10 overflow) |
| `pool_timeout` | 10 s | wait for a pool connection |
| `pool_pre_ping` | `True` | probe r.py: default `False` |
| `pool_recycle` | 1800 s | protection against NAT/load-balancer timeouts |
| `tls` | `"auto"` | see 5.3 |
| `max_rows` | 10 000 | upper bound for `Database.query()` (Studio, tools) |
| `read_only` | `False` | `True`: SQLite `mode=ro` + `query_only`, PostgreSQL `default_transaction_read_only=on`; enforced in the database, not by an SQL filter (Studio) |

Predefined profiles: `Profile.default()`, `Profile.readonly()` (Studio default), `Profile.edge()` (SQLite
`synchronous=NORMAL`, for bulk measurements) and `Profile.testing()` (shorter timeouts). There are no others; own
profiles come from `dataclasses.replace`.

---

## 5. Security concept

### 5.1 Files (SQLite)

1. `check_path(path)` refuses symlinks for the main file, `-wal`, `-shm` and `-journal`. The error message names no
   path; the path is logged at DEBUG only, as in Orkexa.
2. New files are created as `0600` through `O_CREAT|O_EXCL|O_NOFOLLOW`. An existing file keeps its permissions.
   `diagnose()` reports files readable by group or others as finding `TAB-SQ-003`.
3. Backups are written as a `0600` temporary file in the target directory and renamed atomically (`os.replace`)
   after `quick_check`. The target must not be a symlink.
4. Extension loading is off by default.

### 5.2 Error messages and logging

| Rule | Implementation |
|---|---|
| No parameter values in error texts | probes r.py/p.py: SQLAlchemy's `StatementError` contains `[parameters: ...]`. Layer B raises `TabrionError` with `safe_message()`; the original only through `.original`, never as `__cause__` |
| No PostgreSQL `DETAIL` | `DETAIL` contains key values ("Key (email)=(a@b)"). `safe_message` uses only SQLSTATE, kind and constraint, table and column names |
| No password | `safe_url()` masks the password and `password=` parameters in URL and keyword conninfo. `Database.__repr__` uses `safe_url`. SQLAlchemy already masks `repr(url)`; Tabrion does the same for psycopg conninfo strings |
| No SQL text in the default message | the message names the kind, not the statement. SQL text only through `TabrionError.statement`, shortened to 200 characters and without parameters, for debug logs |
| Logging | loggers `tabrion.*`, default WARNING. No parameters at any level. The engine's `echo` is the user's business (documented warning) |

### 5.3 TLS (PostgreSQL) [E]

| `Profile.tls` | Behavior |
|---|---|
| `"auto"` (default) | host `localhost`, `127.0.0.1`, `::1` or a Unix socket: the URL stays unchanged. Otherwise: a missing `sslmode` becomes `require`; `disable`, `allow` or `prefer` in the URL raise `ConfigurationError`. `tabrion.check` reports `TAB-PG-010` as long as `verify-full` is not in effect |
| `"verify-full"` | enforces `sslmode=verify-full`; `sslrootcert` from the URL or the system |
| `"require"` | enforces at least `require` |
| `"insecure"` | no check; `check` reports `TAB-PG-011` as ERROR (local development only) |

Layer A enforces nothing on connections it did not open (existing platforms keep their conninfo).
`tabrion.postgres.diagnose()` reports `ssl=false` for a remote host.

### 5.4 Roles and least privilege

- **Three-role model (kit baseline, documented):** owner (DDL and migrations); runtime (DML only, owns no tables);
  backup (optional, `pg_read_all_data` from PostgreSQL 14).
- **`grant_runtime_role`** unifies the semantics of both consumers, Nodestro and Axentra: it refuses when the role is
  the owner, a superuser, has `BYPASSRLS` or owns tables; it revokes write privileges on bookkeeping tables
  (`protected_tables`); it grants `CONNECT` only when the caller may grant it.
- **`check_role`** is the start check for applications: superuser, `BYPASSRLS` or table ownership give
  `RoleUnsafe`, unless explicitly allowed.
- Tabrion itself needs **no** superuser privileges. The exception is `tabrion.testing` for throwaway databases and
  roles (`CREATEDB`/`CREATEROLE` suffice).
- **RLS compatibility:** Tabrion sets no session scope. Transaction-local settings (`set_config(..., true)`) remain
  the product's business, for example Axentra's `DbScope`. Layer B offers `Database.on_begin(callback)` for that
  (experimental; proven by s.py). `configure()` never sets `row_security` or `role`.

### 5.5 SQL injection

- Every Tabrion statement is parameterised. Identifiers go through `psycopg.sql.Identifier` or are quoted in SQLite.
- Layer B takes SQL text with named parameters (`:name`). Probe P5: `:x::int` collides; the documentation
  recommends `CAST(:x AS int)`.
- Pragma values are validated as enums or integers and are never concatenated as free text.

---

## 6. Data type portability (SQLite and PostgreSQL) [E]

Tabrion provides **rules and profile tests, not a type library**. The reference applications are the executable
specification.

| Kind of data | Rule | SQLite pitfall (probe) | PostgreSQL |
|---|---|---|---|
| Timestamps | always UTC, Python `datetime` with `tzinfo=UTC`; in SQLite ISO 8601 text `YYYY-MM-DDTHH:MM:SS.ffffffZ`. SQLAlchemy: `DateTime(timezone=True)` | SQLite has no time type; naive values stay naive | `timestamptz`, session `TimeZone=UTC` |
| Money, decimals | `Decimal`; SQLAlchemy `Numeric(p, s, asdecimal=True)`; in SQLite stored as **TEXT** or as an integer in the smallest unit (cents) | p.py: `NUMERIC` becomes `float` (rounding errors) | `numeric(p,s)` |
| Integer IDs | `INTEGER PRIMARY KEY` (SQLite) or `bigint generated always as identity`; SQLAlchemy `BigInteger().with_variant(Integer, "sqlite")` | alias of `rowid` only for exactly `INTEGER` | `bigint` |
| UUID | text (36 characters) in SQLite, `uuid` in PostgreSQL; SQLAlchemy `Uuid` | — | `uuid` |
| Boolean | SQLAlchemy `Boolean` (SQLite 0/1 with CHECK) | any number allowed without CHECK | `boolean` |
| Text with a length | check the length in the application or `CHECK(length(x) <= n)`; SQLite `STRICT` tables optional (3.37 or later) | p.py: `VARCHAR(n)` is not enforced | enforced |
| JSON | SQLAlchemy `JSON` (SQLite TEXT + JSON1, PostgreSQL `jsonb` through a variant) | no type check | `jsonb` |
| Foreign keys | always declare them; the profile turns on `foreign_keys=ON` | p.py: default OFF | enforced |
| Measurements (float) | `Double`; avoid NaN/Inf, unit as a column of its own | — | `double precision` |

`tabrion.testing.profiles.portability_cases()` checks these rules against both engines (TAB-T-D01..D09).

---

## 7. Idempotency [E]

**Decision: no upsert or merge helper in 0.1.**

Reasons:

1. `INSERT ... ON CONFLICT` has the same syntax in SQLite (3.24 or later) and PostgreSQL and exists per dialect in
   SQLAlchemy Core (`sqlite.insert().on_conflict_do_nothing`, `postgresql.insert()...`). A helper would only rename
   it.
2. The real idempotency danger is the **retry after an unknown outcome**; finding F-02 shows it in the Nodestro job
   queue. Tabrion addresses it at the root: `Classification.outcome_unknown` and the rule "never retry
   automatically"; a documented pattern with an idempotency key (unique column + `ON CONFLICT DO NOTHING` + read
   back). Use case B uses it (`(device_id, seq)` unique).
3. A generic retry wrapper `Database.run_transaction(fn, attempts=3)` comes **experimentally in 0.2**. It retries
   only `retryable and not outcome_unknown` (SERIALIZATION, DEADLOCK, LOCKED).

---

## 8. Backup, restore, integrity [E]

| Area | In the library | Not in the library (kit documentation `docs/operations/`) |
|---|---|---|
| SQLite | `backup()` as online backup API (like Nodestro's snapshot), target `0600` and atomic, with `quick_check` of the result; `integrity_check`, `quick_check`, `foreign_key_check`; backup required before `migrate` | schedules, rotation, encryption, Litestream |
| PostgreSQL | diagnosis only: `diagnose()` reports `archive_mode`, `wal_level`, whether a role with `pg_read_all_data` exists (findings `TAB-PG-020..022`) | `pg_dump`/`pg_restore`, PITR with pgBackRest/WAL-G, restore drill. The documentation takes over the kit baseline; version rule: restore only to the same or a newer major (finding PG16 ↔ PG17) |
| SQLite restore | `restore()` is **not** included: replacing an open database is product-specific (Nodestro `maintenance()`); the documentation shows the pattern | — |

---

## 9. Diagnosis (`tabrion.check`) [E]

`tabrion.check.check(target)` takes a `sqlite3.Connection`, a psycopg connection or a `Database` and returns
`list[Finding]`. `Database.check()` is the same for a `Database`. `tabrion.check` stays standard-library only: it
checks SQLite itself and loads the PostgreSQL and layer-B collectors (`tabrion.postgres._check`, `tabrion._sa._check`)
by name, keyed on the module of the target's type (contract section 7, G-T1 condition C-1).

`Finding` has the fields `code` (stable, for example `TAB-SQ-001`), `severity` (`info|warning|error`), `title`
(English, short), `detail` (without secrets) and `remedy` (what to do).

| Code | Engine | Finding | Severity |
|---|---|---|---|
| TAB-SQ-001 | SQLite | `foreign_keys` off | warning |
| TAB-SQ-002 | SQLite | `journal_mode` is not WAL (file) | info |
| TAB-SQ-003 | SQLite | file readable by group or others | error |
| TAB-SQ-004 | SQLite | path or side file is a symlink | error |
| TAB-SQ-005 | SQLite | `busy_timeout` < 1000 ms | warning |
| TAB-SQ-006 | SQLite | `quick_check` is not ok | error |
| TAB-SQ-007 | SQLite | SQLite version < 3.37 | warning |
| TAB-SQ-008 | SQLite | WAL file > 64 MiB (checkpoint stuck) | warning |
| TAB-SQ-009 | SQLite | `trusted_schema` on | info |
| TAB-PG-001 | PostgreSQL | role is superuser | error |
| TAB-PG-002 | PostgreSQL | role has `BYPASSRLS` | error |
| TAB-PG-003 | PostgreSQL | role owns tables in the schema | warning |
| TAB-PG-004 | PostgreSQL | `statement_timeout` = 0 | warning |
| TAB-PG-005 | PostgreSQL | `lock_timeout` = 0 | info |
| TAB-PG-006 | PostgreSQL | `idle_in_transaction_session_timeout` = 0 | warning |
| TAB-PG-007 | PostgreSQL | `application_name` empty | info |
| TAB-PG-008 | PostgreSQL | server major < 16 | warning |
| TAB-PG-009 | PostgreSQL | role may `CREATE` in the schema (runtime) | warning |
| TAB-PG-010 | PostgreSQL | remote without `verify-full` | warning |
| TAB-PG-011 | PostgreSQL | remote without TLS | error |
| TAB-PG-012 | PostgreSQL | `transaction_timeout` wanted, server < 17 | info |
| TAB-PG-020 | PostgreSQL | `archive_mode=off` (no PITR possible) | info |
| TAB-PG-021 | PostgreSQL | server is a standby (`in_recovery`) with a write profile | warning |
| TAB-PG-030 | PostgreSQL | extension installed, but its schema is not in the `search_path` | warning |
| TAB-B-001 | layer B | pool without `pre_ping` | info |
| TAB-B-002 | layer B | Alembic schema not current | error |

`expect="owner"` suppresses TAB-PG-003 and TAB-PG-009. Privileges: every check only reads (catalog, `SHOW`). When a
privilege is missing, an `info` finding "not checked" is reported, never an error. Tabrion Studio uses the same
codes.

---

## 10. Stability and versioning [E]

| Term | Meaning in 0.x |
|---|---|
| **stable** | name, signature and semantics change only after a minor version with `DeprecationWarning`; listed in `__all__` |
| **experimental** | may change in any minor version; marked in the docstring and listed as "experimental" in the API contract; no existing platform may use it |
| private | everything starting with `_` and everything not in `__all__` |

| Version | Content | Trigger |
|---|---|---|
| **0.1.0** | layer A **complete**, including `SessionLock`, the advisory helpers, `grant_runtime_role`, `session_configurator` (Axentra is a required consumer); internal vectors (Levuvo Database Profile, ADR 0011); layer B: `Database`, `Profile`, `Transaction`, `transaction`, `read`, `execute`, `fetch_*`, `health` (with `deep`), `check`, `safe_url`; `tabrion.migrations.status`/`require_current`; `tabrion.testing` (SQLite, PostgreSQL schema per test, database per session, roles without superuser, `truncate_tables`, runtime-role mode, environment configuration); reference applications A and B | G-T2 |
| **0.2.0** | Studio support: `Database.query` and `Transaction.query` → `Result` (with `Column`), `Transaction.stream`, `Transaction.cancel`, `inspector()`; experimental `run_transaction`, `on_begin`, `savepoint`. These names are frozen in `docs/api/surface.json` with `since: "0.2"` and are not exported before 0.2. 0.2 also adds the CR-DB modules TAB-CR-1/2 (profile file format, schema fingerprint), whose API is frozen with those work packages, not at G-T1 | before the Studio MVP |
| **0.3.0** (optional) | findings from the migration stages, for example more diagnosis codes | when needed |
| **1.0.0** | API frozen. Preconditions: Axentra and Nodestro integration complete, Orkexa core switched (E-ORK-CORE = yes), both reference applications four weeks without an API break, six-month review positive, owner approval G-R for this publication | G-R |

---

## 11. Quality goals (measurable; details in the security and quality plan)

| Goal | Measure |
|---|---|
| Small | layer A ≤ 2 500 lines without tests, layer B ≤ 1 200 lines [A] |
| Dependency-light | `pip install tabrion` installs 0 third-party packages |
| Correct | the lost-update test (q.py scenario, 5 threads) gives 5 and 0 lock errors on SQLite; a parallel-claim test on PostgreSQL |
| Safe | parameter and password leak tests over all error paths (TAB-T-E10..E14) |
| Covered | branch coverage ≥ 90 % for layer A, ≥ 85 % for layer B |
| Overhead | layer B ≤ 1.3 × plain SQLAlchemy Core for `fetch_one` (probe s.py: SQLAlchemy 154–207 µs against raw 68–83 µs; Tabrion must not make SQLAlchemy noticeably more expensive) |

---

## 12. API stability policy for downstream libraries [E]

Tabrion is a required dependency of Axentra and later possibly of Orkexa. Breaks therefore hit several libraries and
trigger a cascade up to Nodestro (K-34). The following rules apply (see also [ADR 0009](adr/0009-distribution-and-versioning.md)):

| Rule | Content |
|---|---|
| S-1 SemVer 0.x | `0.MINOR.PATCH`: PATCH = fixes only, no signature or behavior change of stable APIs. MINOR = new functions; breaks of **stable** APIs only after S-3. From 1.0 normal SemVer (break = MAJOR) |
| S-2 Scope | stable are all names in `__all__` with status S, the error codes (`TabrionError.code`), the fields of the result dataclasses, the `Kind` values and the MUST rules of the internal profile. Experimental and internal names are excluded; consumer libraries must **not** use experimental names |
| S-3 Deprecation window | a stable name is first marked with `DeprecationWarning`, with replacement and target version. Removal follows at the earliest in the minor version **after next**, at least 8 weeks after the deprecation release. Consumer CI runs with `-W error::DeprecationWarning` |
| S-4 Additive changes | new optional keyword parameters, new dataclass fields at the end, new `Kind` values (consumers must handle `UNKNOWN`) and new error codes are **not** breaks |
| S-5 Behavior changes | security fixes may tighten behavior in a PATCH version, for example stricter redaction; they are listed under `Security` in the changelog, with a notice to every consumer |
| S-6 Check | `scripts/api_surface.py` (TAB-023) compares `__all__`, signatures, dataclass fields and error codes with `docs/api/surface.json`; a change without a matching changelog section fails CI. Golden files (TAB-022a) secure the existing behavior |
| S-7 Downstream early warning | the nightly job `downstream` (TAB-022b) runs the database tests of Axentra, Orkexa[postgres] and Nodestro against Tabrion HEAD. A red run blocks the next Tabrion release |
| S-8 Cascade on a break | (1) Tabrion minor with deprecation and replacement; (2) consumers switch **before** the removal appears, in the order of the edge list: orkexa ∥ axentra → nodestro-runtime → api → sdk → studio; (3) removal at the earliest after S-3 and only when every consumer's master has switched |
