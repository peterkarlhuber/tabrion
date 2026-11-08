# API contract

The binding public API of Tabrion 0.1 and 0.2: every public name, its signature, semantics, errors and test IDs.
This is the English version of planning document 04 (revision 3) and the subject of the API freeze **G-T1**
(TAB-002). Implementation follows in TAB-010..TAB-033.

!!! note "M1 status"
    In M1 every name below exists as a typed stub in `src/tabrion/` with the listed signature and `__all__`; each
    function raises `NotImplementedError` naming its work package. `tests/unit/test_public_api.py` freezes the
    `__all__` list of every module against section 0. Points where the stubs had to decide something the plan leaves
    open are listed under [Open points for G-T1](#open-points-for-g-t1).

!!! note "M2 status"
    `tabrion.errors` (TAB-010), `tabrion.sql` (TAB-011) and `tabrion.config` (TAB-012) are implemented; what the
    implementation settled is listed under [Implementation notes (M2)](#implementation-notes-m2). In
    `tabrion.postgres`, `SessionSettings`, `configure`, `session_configurator`, `ping`, `diagnose` and
    `extension_status` (TAB-015) and `role_info`, `check_role`, `inspect_role` (TAB-016) are implemented (notes
    I-14..I-20, I-34), `SessionLock`, `lock_key` and the advisory helpers (TAB-030, I-32) and `grant_runtime_role`
    (TAB-031, I-35): no function of `tabrion.postgres` is a stub any more. The internal fault proxy of TAB-034 is described in I-33. `tabrion.sqlite` (TAB-013,
    TAB-014) is implemented; its notes (I-21..I-31) are [section 3.9](#39-implementation-notes-tab-013-tab-014). The
    other modules are still stubs.

## Conventions

- Python 3.11 or later and `from __future__ import annotations`. Every returned dataclass is
  `@dataclass(frozen=True, slots=True)`.
- Keyword-only parameters follow `*`.
- Times are passed **in seconds as `float`** (layer B, the Python convention). The exception is `SessionSettings`
  with millisecond `int` values, because PostgreSQL GUCs count in milliseconds and existing code uses milliseconds
  (Axentra `statement_timeout_ms`).
- Tests carry IDs `TAB-T-<area><number>`. Stability: **S** = stable, **X** = experimental.
- Error notation: "maps to `Kind.X`" is a classification; the driver exception stays unchanged (layer A). "raises E"
  means Tabrion raises E.

### Contract rule for connections handed to Tabrion (T-NOTX)

This applies to every layer A function that takes a connection
([ADR 0011](../adr/0011-ldp-internal-and-t-notx.md)).

- Tabrion **never ends a transaction it did not open itself**: no `commit()`/`rollback()` of an open caller
  transaction, no switching of `autocommit`, no change of the row factory, no `SET`/`set_config` except in
  `configure` (and the `session_configurator` built from it).
- If the connection was IDLE at the call (no open transaction), Tabrion may open a short transaction **of its own**
  and ends it itself (read: rollback, write: commit). The status is IDLE again afterwards.
- If a transaction is already running, Tabrion's statements run inside it. The exception are functions that **need**
  a transaction of their own: `grant_runtime_role`, `sqlite.write_transaction`/`read_transaction`/`migrate`. They raise
  `UsageError("...nested_transaction")` instead of changing the foreign transaction.
- The purpose of the context managers `sqlite.write_transaction`/`read_transaction` is explicitly transaction control;
  they count as "opened by Tabrion".
- **Checked against the consumers:**
  - Axentra `ping`/`role_info` (`database.py:348-382`) take a fresh pool connection, read, and roll back only their
    own implicit transaction. That follows the rule and is taken over one to one.
  - Axentra `joined()` and the savepoint join stay Axentra code. Tabrion functions called inside them run in the host
    transaction and do not end it. Functions that need their own transaction (`grant_runtime_role`) raise
    `UsageError` instead of opening `connection.transaction()` as a savepoint in the foreign transaction.
  - Nodestro `_transaction` (`storage_postgres.py:360-383`) calls Tabrion only inside its own transaction; no Tabrion
    function commits there.
  - Test TAB-T-G01 checks `connection.info.transaction_status` before and after every layer A function in three
    states: IDLE with autocommit, IDLE without autocommit, INTRANS. INTRANS stays INTRANS, IDLE stays IDLE. The only
    documented exception: `configure` on IDLE without autocommit leaves INTRANS (section 4.1, no commit).

### RLS safety (R-SET)

- Tabrion sets **only** session GUCs from a fixed allowlist: `statement_timeout`, `lock_timeout`,
  `idle_in_transaction_session_timeout`, `transaction_timeout`, `application_name`, `search_path`, `TimeZone`.
- It never sets `role`/`session_authorization`, `row_security`, `app.*` or other custom prefixes. Security contexts
  such as Axentra's `DbScope` with `set_config(..., true)` stay transaction-local and Axentra code.
- The allowlist is fixed in code; other names cannot be configured (`ConfigurationError("postgres.setting_not_allowed")`).
- Tabrion never calls `RESET ALL` or `DISCARD`; that would destroy host settings in shared pools (M10).
- Test TAB-T-G02: after every Tabrion function on a pool connection, `current_setting('app.tenant_id', true)` and
  `current_user` are unchanged. Test TAB-T-G03 checks the allowlist.

## 0. Overview of the public names

| Module | Names | Since | Stab. | Main users |
|---|---|---|---|---|
| `tabrion.errors` | `Kind`, `Classification`, `classify`, `safe_message`, `DbErrorRedactionFilter`, `TabrionError` and subclasses (section 1.4) | 0.1 | S | **AX**, **N**, **O**, apps |
| `tabrion.sql` | `split_statements` | 0.1 | S | **AX** (runner `no-transaction`), Studio, apps |
| `tabrion.config` | `safe_url` (also as `tabrion.safe_url`), `url_from_env` | 0.1 | S | **N**, Studio, apps |
| `tabrion.sqlite` | `SQLiteProfile` (with `SQLiteProfile.orkexa()`, `.nodestro()`), `JournalMode`, `Synchronous`, `connect`, `apply_profile`, `check_path`, `UnsafePath`, `create_private_file`, `loading_extensions`, `write_transaction`, `read_transaction`, `migrate`, `MigrationResult`, `backup`, `BackupResult`, `integrity_check`, `quick_check`, `IntegrityReport`, `foreign_key_check`, `ForeignKeyViolation`, `diagnose`, `SQLiteDiagnosis` | 0.1 | S | **N** (core `SQLiteStore`, K-32), **O** (E-ORK-CORE = yes), apps, Studio |
| `tabrion.postgres` | `SessionSettings`, `configure`, `session_configurator`, `ping`, `PingResult`, `diagnose`, `PostgresDiagnosis`, `extension_status`, `ExtensionStatus` | 0.1 | S | **AX**, **N**, **O**, layer B, Studio |
| `tabrion.postgres` | `RoleInfo`, `RoleReport`, `role_info`, `check_role`, `inspect_role`, `grant_runtime_role` | 0.1 | S | **AX**, **N**, apps, Studio |
| `tabrion.postgres` | `SessionLock`, `lock_key`, `try_advisory_lock`, `wait_advisory_lock`, `advisory_unlock` | 0.1 | S | **AX** (runner, worker), **N**, apps |
| `tabrion.check` | `check`, `Finding`, `Severity` | 0.1 | S | Studio, apps |
| `tabrion` (layer B) | `Database`, `Profile`, `TLSMode`, `Transaction`, `Health` | 0.1 | S | apps, Studio |
| `tabrion.migrations` (extra `migrations`) | `status`, `require_current`, `SchemaStatus` | 0.1 | S | apps, Studio |
| `tabrion` (layer B) | `Result`, `Column`, `Database.query`, `Transaction.query`, `Transaction.stream`, `Transaction.cancel`, `Database.inspector` | 0.2 | S | Studio |
| `tabrion` (layer B) | `Database.run_transaction`, `Database.on_begin`, `Transaction.savepoint` | 0.2 | X | apps |
| `tabrion.testing` | `configure`, fixtures (section 6) including a database per session, `RoleSetup`, `truncate_tables`, roles without superuser, `require_extension`, `profiles.*` | 0.1 | S | **AX**, **N**, **O** (dev extra), apps |
| *(internal)* | Vectors `conformance/` and the loader `tests/support/conformance.py` (sections 10, 11) | - | internal | Tabrion's own tests **only** (K-35) |

Legend: **AX** = Axentra (required dependency `tabrion[postgres]`, K-30); **N** = Nodestro (core and server
profile, K-32); **O** = Orkexa (`orkexa[postgres]` to `tabrion[postgres]`, K-31; core per E-ORK-CORE).

`grant_runtime_role`, `SessionLock` and the advisory helpers ship in **0.1** already, because Axentra needs them as a
required consumer (plan document 06).

The `__all__` lists in the code (frozen by `tests/unit/test_public_api.py`) are:

| Module | `__all__` |
|---|---|
| `tabrion` | `Database`, `Health`, `Profile`, `TLSMode`, `TabrionError`, `Transaction`, `__version__`, `safe_url` (0.2 adds `Column`, `Result`) |
| `tabrion.errors` | `Kind`, `Classification`, `classify`, `safe_message`, `DbErrorRedactionFilter`, and the 25 error classes of section 1.4 |
| `tabrion.sql` | `split_statements` |
| `tabrion.config` | `safe_url`, `url_from_env` |
| `tabrion.sqlite` | the names of section 3, plus `Migration` |
| `tabrion.postgres` | the names of section 4 (including the role and lock names, re-exported from `tabrion.postgres.roles` and `tabrion.postgres.locks`) |
| `tabrion.check` | `check`, `Finding`, `Severity` |
| `tabrion.migrations` | `status`, `require_current`, `SchemaStatus` |
| `tabrion.testing` | `configure`, `profiles`, the fixtures of section 6 (including `tabrion_role_setup`), `postgres_server_version`, `truncate_tables`, `require_extension`, `RoleSetup`, `TestRoles`, `PostgresSchema`, `PostgresDatabaseHandle` |
| `tabrion.testing.profiles` | `SQLiteConnect`, `ProfileCase`, `PortabilityCase`, `sqlite_profile_cases`, `portability_cases` |

**0.2 names are not exported before 0.2** (G-T1 condition C-6). `Result`, `Column` and the 0.2 methods of
`Database` and `Transaction` are frozen in [`docs/api/surface.json`](surface.json) with `since: "0.2"`, and their stubs
are staged in a private module until TAB-033 mixes them in. `scripts/api_surface.py` (TAB-023, CI job "api surface")
compares the code with that file on every pull request: a changed signature fails, and a name whose `since` is newer
than the package version must not be importable from its public path.

Layer B names are exported lazily (PEP 562). Without SQLAlchemy, `tabrion.Database` raises `ImportError` with the
hint `pip install "tabrion[sqlalchemy]"`; `tabrion.migrations` without Alembic and `tabrion.testing` without pytest
behave the same way with their extras.

## 1. `tabrion.errors` (standard library)

### 1.1 `Kind`

```python
class Kind(enum.StrEnum):
    CONNECTION = "connection"  # connection gone or refused, server shutdown
    TIMEOUT = "timeout"  # statement_timeout, idle-in-transaction timeout, Tabrion deadline
    CANCELED = "canceled"  # explicit cancel
    LOCKED = "locked"  # SQLite BUSY/LOCKED, PG lock_not_available (lock_timeout)
    DEADLOCK = "deadlock"
    SERIALIZATION = "serialization"
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    NOT_NULL = "not_null"
    CHECK = "check"
    EXCLUSION = "exclusion"
    DATA = "data"  # invalid value, overflow, type
    UNDEFINED_OBJECT = "undefined_object"  # table, column or function missing
    SYNTAX = "syntax"
    PERMISSION = "permission"
    READ_ONLY = "read_only"  # write attempt in a read-only transaction or file
    CORRUPT = "corrupt"
    DISK_FULL = "disk_full"
    RESOURCE = "resource"  # memory, too many connections
    UNKNOWN = "unknown"
```

### 1.2 `Classification`

```python
@dataclass(frozen=True, slots=True)
class Classification:
    kind: Kind
    retryable: bool  # the WHOLE transaction may be retried
    outcome_unknown: bool  # statement or COMMIT may have run (connection lost during execution)
    connection_usable: bool  # the connection can still be used after the error
    driver: Literal["sqlite3", "psycopg", "other"]
    sqlstate: str | None  # PG SQLSTATE (5 characters)
    sqlite_code: str | None  # e.g. "SQLITE_BUSY" (sqlite3.Error.sqlite_errorname)
    constraint: str | None  # PG diag.constraint_name; SQLite: "<table>.<column>[,...]" from the message
    table: str | None
    column: str | None
```

The fields are explicitly **without values**: `DETAIL`, `HINT` and `str(exc)` are not taken over.

### 1.3 Functions

```python
def classify(error: BaseException, *, during_commit: bool = False, sent: bool = True) -> Classification
def safe_message(error: BaseException | Classification) -> str
```

- **`classify`** recognizes errors by duck typing.
  - psycopg errors: `sqlstate`/`diag`; a broken connection: `psycopg.OperationalError` with `conn.broken`.
  - `sqlite3.Error`: `sqlite_errorname`, with a fallback to text recognition like Orkexa's `is_corruption`
    (`_storage.py:118-126`).
  - SQLAlchemy: `DBAPIError.orig` is unwrapped without importing SQLAlchemy.
  - Anything unknown gives `Kind.UNKNOWN`, `retryable=False`.
  - `outcome_unknown=True` when `kind == CONNECTION` and (`sent` or `during_commit`). Callers that know nothing was
    sent (checkout errors) pass `sent=False`.
  - **Chains:** `classify` looks for the driver error along `.orig` (SQLAlchemy), `__cause__` and `__context__` up to
    depth 5. It takes the first error with a `sqlstate` or `sqlite_errorname`; if there is none, it classifies the
    outermost object. Amended by implementation note I-13: `__context__` only as a fallback, and an outer connection
    loss wins.
  - It never raises.
- **`safe_message`**: an English text of the form `"<Kind text> (SQLSTATE 23505, constraint parts_pkey)"`, built only
  from `Classification` fields, never from `str(exc)`. So psycopg's `DETAIL` (which holds key values such as email
  addresses, B-06-1), `HINT`, `CONTEXT`, parameters, paths and passwords are absent. Chains are resolved as in
  `classify`.

**Mapping table** (to be implemented completely, TAB-T-E01..E09):

| Source | Kind | retryable | connection_usable |
|---|---|---|---|
| PG `08000`, `08003`, `08006`, `08001`, `08004`; psycopg `OperationalError` with `broken` | CONNECTION | yes, if `not outcome_unknown` | no |
| PG `57P01` admin_shutdown, `57P02`, `57P03` cannot_connect_now | CONNECTION | yes, if `not outcome_unknown` | no |
| PG `53300` too_many_connections | CONNECTION | yes | no |
| PG `57014` query_canceled with text "statement timeout" / Tabrion's own timeout | TIMEOUT | no | yes |
| PG `57014` otherwise | CANCELED | no | yes |
| PG `25P03` idle_in_transaction_session_timeout | TIMEOUT | no | no |
| PG `55P03` lock_not_available | LOCKED | yes | yes |
| PG `40P01` | DEADLOCK | yes | yes |
| PG `40001` | SERIALIZATION | yes | yes |
| PG `23505` / `23503` / `23502` / `23514` / `23P01` | UNIQUE / FOREIGN_KEY / NOT_NULL / CHECK / EXCLUSION | no | yes |
| PG class `22` | DATA | no | yes |
| PG `42P01`, `42703`, `42883`, `42704`, `3F000` | UNDEFINED_OBJECT | no | yes |
| PG `42601` | SYNTAX | no | yes |
| PG `42501` | PERMISSION | no | yes |
| PG `25006` | READ_ONLY | no | yes |
| PG `XX001`, `XX002` | CORRUPT | no | yes |
| PG `53100` | DISK_FULL | no | yes |
| PG `53200`, class `54000` | RESOURCE | no | yes |
| SQLite `SQLITE_BUSY*`, `SQLITE_LOCKED*` | LOCKED | yes | yes |
| SQLite `SQLITE_CONSTRAINT_UNIQUE`, `_PRIMARYKEY` | UNIQUE | no | yes |
| SQLite `SQLITE_CONSTRAINT_FOREIGNKEY` / `_NOTNULL` / `_CHECK` | FOREIGN_KEY / NOT_NULL / CHECK | no | yes |
| SQLite `SQLITE_READONLY*` | READ_ONLY | no | yes |
| SQLite `SQLITE_CORRUPT*`, `SQLITE_NOTADB` | CORRUPT | no | no |
| SQLite `SQLITE_FULL` | DISK_FULL | no | yes |
| SQLite `SQLITE_INTERRUPT` | CANCELED (TIMEOUT for a Tabrion deadline) | no | yes |
| SQLite `SQLITE_AUTH` | PERMISSION | no | yes |
| SQLite `SQLITE_ERROR` with "no such table/column" | UNDEFINED_OBJECT | no | yes |
| SQLite `SQLITE_ERROR` with "syntax error" | SYNTAX | no | yes |
| SQLite `SQLITE_CANTOPEN`, `SQLITE_IOERR*` | CONNECTION | no | no |

```python
class DbErrorRedactionFilter(logging.Filter):
    def __init__(self, name: str = "") -> None
    def filter(self, record: logging.LogRecord) -> bool   # always returns True
```

- A **`logging.Filter`** for the consumers' handlers or loggers (Axentra AX-8, Nodestro, Orkexa OR-3). It suppresses
  nothing; it **sanitizes**: if `record.exc_info` has a database exception in its chain (`__cause__`/`__context__`,
  psycopg, sqlite3, SQLAlchemy `DBAPIError`), the formatted traceback is replaced by a version in which every
  exception line carries `safe_message(exc)` (no `DETAIL`/`HINT`/`CONTEXT` value, no `[parameters]`). `record.args`
  that are database exceptions themselves are replaced by `safe_message`. The SQLSTATE and the constraint name stay,
  also as the attributes `record.db_sqlstate`/`record.db_constraint` for JSON formatters.
- The filter changes only the record (the preformatted `record.exc_text`), never the exception; stack trace lines
  (files, line numbers) stay.
- The consumer installs it; Tabrion installs it nowhere globally.
- Tests: TAB-T-E21 (a unique violation with an email in `DETAIL`: the value is missing in text and JSON output,
  `sqlstate=23505` and the constraint are present), E22 (chained exception), E23 (SQLAlchemy `[parameters]`), E24
  (a non-database exception is unchanged).

**Coverage of the Axentra patterns** (grep of `axentra/src`, master 40c0f2c; 16 typed and 4 generic places):

| Axentra today | Places | Tabrion |
|---|---|---|
| `except pg_errors.UniqueViolation` | 9: tenancy 212/734, notify 331/767/1203, usage 275, access/roles 144, billing 356/578/1048 | `classify(e).kind is Kind.UNIQUE`; `constraint` tells several unique indexes apart |
| `except pg_errors.ForeignKeyViolation` | 4: tenancy 214, usage 371, billing 451 | `Kind.FOREIGN_KEY` |
| `except pg_errors.CheckViolation` | 1: access/roles 142 | `Kind.CHECK` |
| `except psycopg.errors.QueryCanceled` | 1: search/adapters 297 | `Kind.TIMEOUT`/`CANCELED` |
| `except psycopg.errors.InsufficientPrivilege` | 1: data/roles 182 | `Kind.PERMISSION` (handled inside `grant_runtime_role`) |
| `except psycopg.Error` (generic) | 4: search/adapters 123, events/worker 358, events/schedules 90/95 | `classify(e)` gives `connection_usable` and `retryable` for worker decisions |
| Serialization, deadlock, lock timeout | **not** caught today | `Kind.SERIALIZATION`, `Kind.DEADLOCK`, `Kind.LOCKED` (55P03) ready once Axentra sets `lock_timeout` |

Rule for Axentra: typed `except` branches with their own value-free answers (`Conflict(...) from None`) may stay;
psycopg classes are stable. `classify`/`safe_message` replace the generic paths and every place where error text is
logged or returned. That fixes B-06-1 (no `DETAIL` with values).

### 1.4 Error hierarchy (raised by Tabrion)

```python
class TabrionError(Exception):
    code: str                      # stable code, e.g. "sqlite.schema_too_new"
    classification: Classification | None
    original: BaseException | None # NEVER chained as __cause__ (raise ... from None)
    statement: str | None          # at most 200 characters, without parameters; layer B only
    details: Mapping[str, str | int | None]   # e.g. {"found": 5, "supported": 4}; never data values
    def __str__(self) -> str: ...  # the safe message
    def __repr__(self) -> str: ... # class and code, no text from original

class UsageError(TabrionError)              # misuse: nested transaction, closed database ...
class ConfigurationError(TabrionError)      # invalid profile, TLS rule violated, environment missing
class DatabaseError(TabrionError)           # layer B: translated driver errors; .kind == classification.kind
    class ConnectionFailed(DatabaseError)        # CONNECTION, RESOURCE (53300)
    class StatementTimeout(DatabaseError)        # TIMEOUT
    class Canceled(DatabaseError)                # CANCELED
    class TransientError(DatabaseError)          # LOCKED, DEADLOCK, SERIALIZATION
    class IntegrityViolation(DatabaseError)      # UNIQUE, FOREIGN_KEY, NOT_NULL, CHECK, EXCLUSION
    class PermissionDenied(DatabaseError)        # PERMISSION
    class ReadOnlyViolation(DatabaseError)       # READ_ONLY
    class StatementError(DatabaseError)          # SYNTAX, UNDEFINED_OBJECT, DATA
    class StorageCorrupt(DatabaseError)          # CORRUPT, DISK_FULL
    class UnknownDatabaseError(DatabaseError)
class MultipleRowsError(TabrionError)       # fetch_one/fetch_scalar with more than one row
class MigrationError(TabrionError)          # tabrion.sqlite.migrate
    class SchemaTooNew(MigrationError)           # code "sqlite.schema_too_new", details found/supported
    class MigrationMissing(MigrationError)       # "sqlite.migration_missing", details found/supported/missing
    class BackupRequired(MigrationError)         # "sqlite.backup_required"
    class BackupFailed(MigrationError)           # "sqlite.backup_failed" (original set)
    class MigrationFailed(MigrationError)        # "sqlite.migration_failed", details step
class SchemaNotCurrent(TabrionError)        # layer B Alembic gate, details current/head
class RoleMissing(TabrionError)             # "postgres.role_missing"
class RoleUnsafe(TabrionError)              # "postgres.role_unsafe", details reasons (comma list)
class UnsafePath(TabrionError)              # "sqlite.symlink" / "sqlite.unsafe_path" / "sqlite.unsafe_owner" / "sqlite.unsafe_mode"
```

**Drop-in rule:** the codes equal the Nodestro codes without the prefix `storage.`. Nodestro maps
`TabrionError.code` by table to `NodestroError("storage.<suffix>")` (plan document 06, MIG-NOD).

**Tests:**

- TAB-T-E01..E09: mapping per table row, with real errors from SQLite and PostgreSQL 16, 17 and 18.
- TAB-T-E10: `str`, `repr` and `safe_message` never contain the parameter value `"SECRET-4711"`; checked over every
  kind and for a PG `DETAIL` with a unique violation.
- TAB-T-E11: no `__cause__` chain.
- TAB-T-E12: the conninfo password appears in no error (connecting to a wrong host).
- TAB-T-E13: `classify` never raises, tested with 1 000 random exceptions (Hypothesis).
- TAB-T-E14: `outcome_unknown` checked by proxy fault injection (reusing the probe `jobqueue_retry_probe.py`).
- TAB-T-E15: with `lc_messages=de_DE`, 57014 is classified as CANCELED without a crash (R-T02).
- TAB-T-E16: `import tabrion.errors` loads neither psycopg nor SQLAlchemy.

## 2. `tabrion.sql` and `tabrion.config` (standard library)

### 2.1 `split_statements`

```python
def split_statements(sql: str) -> list[str]
```

**Purpose:** split SQL at top-level semicolons (migrations without a transaction, the Studio editor).

**Behavior:**

- Recognizes `'...'` with doubled quotes, `E'...'` with backslash escapes, `"..."` identifiers, `$tag$...$tag$`, `--`
  comments and **nested** `/* /* */ */` (PostgreSQL semantics).
- Empty statements and statements that are only comments are dropped. Comments inside a statement stay.
- The result is `strip()`-ped.

**Errors:** none. An unterminated quote or comment yields the rest as the last statement, as in Axentra
`migrations.py:125-171`.

**Identity with the Axentra splitter** (required; Axentra is a consumer, and `migrations.py:125-171` is replaced by a
re-export):

- For every input that Axentra's splitter splits correctly, the output is **byte-identical**. It is checked against
  golden files: the Axentra test vectors and the splitter output of every shipped Axentra migration file (TAB-022a,
  TAB-T-Q01, Q02).
- Differences exist only for inputs that Axentra splits wrongly today: `E'...\'...'` and nested `/* /* */ */`. They
  are documented in the Axentra CHANGELOG as a fix.
- Before the switch, MIG-AXE proves that no shipped or registered Axentra migration contains such constructs.
  Checksums depend on the file text anyway, not on the splitter.
- SQLite note: SQLite has no dollar quotes; this is harmless (TAB-T-Q09). The function is pure and thread-safe.

**Tests:** TAB-T-Q01..Q10 (Axentra vectors, named dollar tags, `E'\''`, nested comments, a semicolon in a comment, a
`CREATE FUNCTION` body, empty input).

### 2.2 No shared checksum function

Nodestro (`storage_postgres.py:155-158`) and Axentra (`migrations.py:85-86`) use the same one-liner
`hashlib.sha256(sql.encode("utf-8")).hexdigest()`. A public Tabrion function would add coupling without benefit, so
there is none. The one-liner stays in both platforms.

### 2.3 `safe_url`, `url_from_env`

```python
def safe_url(url: str) -> str
def url_from_env(name: str, *, environ: Mapping[str, str] | None = None, required: bool = True) -> str | None
```

- **`safe_url`** masks the password in URL form (`postgresql://u:***@h/db`, also `postgresql+psycopg://`) and in
  keyword conninfo (`password=***`). A query parameter `password` is masked too. Unparseable input returns
  `"<unparseable url>"`. It never raises.
- **`url_from_env`** reads `name` or `name + "_FILE"`. For a file, the content without the trailing newline counts;
  the file must be a regular file, symlinks are allowed. If both are set, it raises
  `ConfigurationError("config.ambiguous")`, as in Nodestro `server_settings.py:115-141`. If neither is set and
  `required=True`, it raises `ConfigurationError("config.missing")`. No message contains a value.
- **Tests:** TAB-T-C01..C08.

## 3. `tabrion.sqlite` (standard library)

### 3.1 Types

```python
class JournalMode(enum.StrEnum): WAL = "wal"; DELETE = "delete"; TRUNCATE = "truncate"
class Synchronous(enum.StrEnum): OFF = "off"; NORMAL = "normal"; FULL = "full"; EXTRA = "extra"

@dataclass(frozen=True, slots=True)
class SQLiteProfile:
    journal_mode: JournalMode = JournalMode.WAL
    synchronous: Synchronous = Synchronous.FULL
    foreign_keys: bool = True
    busy_timeout: float = 30.0          # seconds
    secure_delete: bool = True
    trusted_schema: bool = False
    file_mode: int = 0o600
    allow_shared_file: bool = False     # M1 proposal, see open points

    @classmethod
    def orkexa(cls) -> SQLiteProfile    # WAL, FULL, busy 30 s, secure_delete=True  (ORK/_storage.py:144-166)
    @classmethod
    def nodestro(cls) -> SQLiteProfile  # WAL, FULL, busy 30 s, secure_delete=False (RT/storage.py:280-282)

Migration = Callable[[sqlite3.Connection], None]

def create_private_file(path: str | os.PathLike[str]) -> None
    # O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW, 0600; an existing file stays unchanged; ":memory:" is ignored
```

The default `SQLiteProfile()` is called `default` in plan document 06. Both existing profiles set
`foreign_keys=True` (plan document 07 DI-01); that is the only deliberate change from today's behavior and is secured
at adoption with `foreign_key_check` on the existing files. `create_private_file` equals Orkexa
`_storage.py:80-93` plus `O_NOFOLLOW`.

`__post_init__` validates `busy_timeout >= 0` and `file_mode & 0o077 == 0`. Other modes are possible only with
`allow_shared_file=True` (not recommended). Errors: `ConfigurationError`.

### 3.2 `connect`

```python
def connect(
    path: str | os.PathLike[str],
    *,
    profile: SQLiteProfile = SQLiteProfile(),
    read_only: bool = False,
    create: bool = True,
    check_same_thread: bool = False,
) -> sqlite3.Connection
```

**Steps:**

1. `":memory:"` skips every file step.
2. `check_path(path)`.
3. If `create` holds, the file is missing and `read_only` is not set: create the file with
   `os.open(O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW, file_mode)`.
4. `sqlite3.connect(path, isolation_level=None, timeout=profile.busy_timeout, check_same_thread=...)`; with
   `read_only` as the URI `file:<quoted>?mode=ro`.
5. `apply_profile`.
6. On an error the connection is closed and the error passed on.

**Errors:** `UnsafePath` (symlink); `FileNotFoundError` when `create=False` or `read_only` is set and the file is
missing; `sqlite3.DatabaseError` (maps to `CORRUPT`) when the file is not a database (passed through unchanged).

**Side effects:** creates a file; sets `journal_mode=WAL` permanently in the file.

**Thread safety:** the connection has no internal lock (the caller serializes, specification section 3.3).

**Compared with existing code:** Orkexa `connect_private_database` (`_storage.py:146-166`) is covered, plus
`foreign_keys`, `trusted_schema` and `synchronous`. Nodestro `SQLiteStore.__init__` (`storage.py:271-286`) is
covered, plus permissions, the symlink check and foreign keys; Nodestro passes `SQLiteProfile(secure_delete=False)`
to keep today's behavior.

**Tests:** TAB-T-S01 file 0600 despite umask 022; S02 an existing file keeps its mode; S03-S06 symlink rejection for
the main, `-wal`, `-shm` and `-journal` files; S07 race (a symlink between the check and `open`) fails on
`O_NOFOLLOW`/`O_EXCL`; S08 `read_only` does not write, `INSERT` gives READ_ONLY; S09 `:memory:`; S10 the pragma
values after `connect`; S11 a broken file gives CORRUPT; S12 Windows: no `O_NOFOLLOW`, only the symlink check there
(documented).

### 3.3 `apply_profile`, `check_path`, `loading_extensions`

```python
def apply_profile(connection: sqlite3.Connection, profile: SQLiteProfile = SQLiteProfile(), *,
                  file_backed: bool | None = None) -> None
def check_path(path: str | os.PathLike[str]) -> None           # raises UnsafePath
@contextmanager
def loading_extensions(connection: sqlite3.Connection) -> Iterator[None]
```

- **`apply_profile`** sets `busy_timeout`, `foreign_keys`, `synchronous`, `secure_delete`, `trusted_schema` and
  `journal_mode` (file-backed only; `file_backed=None` detects it through `PRAGMA database_list`). It is idempotent.
  It raises `UsageError` while a transaction is open, because `foreign_keys` has no effect there. Layer B calls it in
  the `connect` event.
- **`loading_extensions`** enables `enable_load_extension(True)` and disables it in `finally`. Without that function in
  the Python build it raises `UsageError("sqlite.extensions_unavailable")`. Pattern: Orkexa `index.py:176-194`.
- **Tests:** TAB-T-S13..S15.

### 3.4 `write_transaction`, `read_transaction`

```python
@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]
@contextmanager
def read_transaction(connection: sqlite3.Connection, *, enforce: bool = True) -> Iterator[sqlite3.Connection]
```

- **`write_transaction`**: `BEGIN IMMEDIATE`; commit at the normal end; `ROLLBACK` on `BaseException`, suppressing
  rollback errors when `connection.in_transaction` is already false. `UsageError("sqlite.nested_transaction")` when
  `connection.in_transaction` already holds. `isolation_level` must be `None`, otherwise `UsageError`.
- **`read_transaction`**: `BEGIN` (deferred, a consistent snapshot). With `enforce=True` also `PRAGMA query_only=ON`
  before `BEGIN` and `OFF` afterwards. A write attempt classifies as READ_ONLY
  (`sqlite3.OperationalError: attempt to write a readonly database`).
- **Semantics:** like Nodestro `_transaction` (`storage.py:313-332`) but **without** reentrancy and without a lock.
  Nodestro keeps its counter and RLock and calls `write_transaction` only at the outermost level.
- **Thread safety:** the caller serializes.
- **Tests:** TAB-T-S16 lost-update scenario from q.py, 5 threads with their own connections, result 5, 0 errors;
  S17 an exception gives a rollback; S18 nesting gives UsageError; S19 `read_transaction` blocks writes; S20
  `query_only` is OFF again after the end and after an exception; S21 `KeyboardInterrupt` in the block gives a
  rollback.

### 3.5 `migrate`

```python
@dataclass(frozen=True, slots=True)
class MigrationResult:
    previous: int
    current: int
    backed_up: bool

def migrate(
    connection: sqlite3.Connection,
    migrations: Mapping[int, Migration],
    *,
    target: int,
    backup: Callable[[int, int], None] | None,
    is_fresh: Callable[[sqlite3.Connection], bool] | None = None,
) -> MigrationResult
```

**Semantics** (one to one with Nodestro `storage.py:563-637`):

1. An open transaction raises `UsageError`.
2. `current = PRAGMA user_version`.
3. `current == target` returns `MigrationResult(current, current, False)` and writes nothing.
4. `current > target` raises `SchemaTooNew` and writes nothing.
5. Missing steps in `current+1..target` raise `MigrationMissing`.
6. `is_fresh` (hook: is the database new and empty) defaults to true when no table other than `sqlite_%` exists. If
   the database is not empty and `backup is None`, it raises `BackupRequired`.
7. If the database is not empty, `backup(current, target)` runs. Any exception from it raises `BackupFailed` with
   `original` set and **without** `{exc}` in the text (fixing the side finding of the consolidation report).
8. All steps and `PRAGMA user_version = target` run in **one** `write_transaction`. An exception raises
   `MigrationFailed(details={"step": n})`, and the database stays unchanged.

| Class | `code` | `details` | `original` |
|---|---|---|---|
| SchemaTooNew | `sqlite.schema_too_new` | `found`, `supported` | - |
| MigrationMissing | `sqlite.migration_missing` | `found`, `supported`, `missing` | - |
| BackupRequired | `sqlite.backup_required` | `found`, `supported` | - |
| BackupFailed | `sqlite.backup_failed` | `found`, `supported` | the backup exception |
| MigrationFailed | `sqlite.migration_failed` | `found`, `supported`, `step` | the step exception |

**Note:** DDL in SQLite is transactional. `PRAGMA foreign_keys` inside a step has no effect; this is documented.
Steps that rebuild tables use the twelve-step pattern of the SQLite documentation.

**Tests:** TAB-T-S22..S30. Every path checks that the file is unchanged after the error (byte comparison); the backup
order is checked; parallel calls from two processes are tested (the second sees the new version and does nothing).

### 3.6 `backup`

```python
@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    pages: int
    bytes: int
    quick_check: str        # "ok" or the first message

def backup(
    connection: sqlite3.Connection,
    target: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    pages_per_step: int = -1,
    verify: bool = True,
) -> BackupResult
```

**Steps:**

1. `check_path(target)`. If the target exists and `overwrite=False`, raise `FileExistsError`.
2. Create a temporary file `target.<rand>.tmp` with 0600 in the same directory.
3. `connection.backup(dest, pages=pages_per_step)`.
4. Close the target. With `verify`, run `quick_check` on a separate read-only connection to the temporary file; if
   the result is not `ok`, delete it and raise `TabrionError("sqlite.backup_corrupt")`.
5. `fsync`, then `os.replace`.
6. The temporary file is deleted on every error.

**Semantics:** a consistent online snapshot. Other connections may keep reading and writing (SQLite restarts on
writes; with `pages=-1` the copy is one step). The connection must not have an open write transaction of the same
caller, otherwise `UsageError`.

**Existing code:** Nodestro `snapshot` (`storage.py:649-658`) is covered, plus permissions, atomic replacement and
the check. Nodestro `workspace.py:221-235` (the pre-migration copy) will use `backup`.

**Tests:** TAB-T-S31..S34 (permissions, atomicity on abort, check, parallel writes).

### 3.7 `integrity_check`, `quick_check`, `foreign_key_check`

```python
@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    messages: tuple[str, ...]     # SQLite messages (page and table names, no row data)
    truncated: bool

@dataclass(frozen=True, slots=True)
class ForeignKeyViolation:
    table: str
    rowid: int | None
    parent: str
    fk_index: int

def integrity_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport
def quick_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport
def foreign_key_check(connection: sqlite3.Connection, *, table: str | None = None,
                      limit: int = 1000) -> list[ForeignKeyViolation]
```

Read-only; outside a transaction the checks run in a `read_transaction`. A CORRUPT file gives `ok=False`, not an
exception, as long as it opens at all. `table` is quoted. Tests TAB-T-S35..S38, including a deliberately damaged
file.

### 3.8 `diagnose`

```python
@dataclass(frozen=True, slots=True)
class SQLiteDiagnosis:
    sqlite_version: str
    path: str | None               # None for :memory:
    file_mode: int | None
    is_symlink: bool
    journal_mode: str
    synchronous: str
    foreign_keys: bool
    busy_timeout_ms: int
    secure_delete: bool
    trusted_schema: bool
    query_only: bool
    user_version: int
    page_size: int
    page_count: int
    freelist_count: int
    wal_bytes: int | None
    compile_options: tuple[str, ...]   # filtered to ENABLE_FTS5, ENABLE_JSON1, THREADSAFE ...
    quick_check: str | None            # only when run_quick_check=True

def diagnose(connection: sqlite3.Connection, *, run_quick_check: bool = False) -> SQLiteDiagnosis
```

Read-only, no side effects. The basis for `tabrion.check` (TAB-SQ-*) and for nodestro-api `diagnostics.py:110-133`
(MIG-NOD). Tests TAB-T-S39..S41.

### 3.9 Implementation notes (TAB-013, TAB-014)

Points the `tabrion.sqlite` implementation settled where sections 3.1 to 3.8 leave room. None changes a signature or
a name of `surface.json`; each is recorded in the deviation log of [STATUS.md](../program/STATUS.md#deviation-log).
Every `UsageError` and `ConfigurationError` below carries only the code and value-free details.

| # | Section | Note |
|---|---|---|
| I-21 | 3.1 | `SQLiteProfile.__post_init__` raises `ConfigurationError("sqlite.invalid_profile", details={"field": ...})`. Beyond the two rules of 3.1: `journal_mode` and `synchronous` given as strings are converted to the enum member (other strings are refused, so no caller text reaches a `PRAGMA`); the four flags must be `bool`; `busy_timeout` must be a finite number from 0 to 2 147 483.647 s (SQLite's C `int` of milliseconds); `file_mode` must be an `int` from 0 to `0o777` that keeps the owner's read and write bits |
| I-22 | 3.2 | `connect` refines the steps: (a) `""` is a private temporary database and is treated like `":memory:"`; (b) an existing file is opened with `O_RDONLY\|O_NOFOLLOW\|O_NONBLOCK` and `fstat`ed: it must be a regular file, otherwise `UnsafePath("sqlite.unsafe_path")` (a directory, FIFO or device), and a link gives `sqlite.symlink`; on POSIX, unless the profile sets `allow_shared_file`, it must belong to the effective user (`UnsafePath("sqlite.unsafe_owner")`) and must not be group- or world-writable (`UnsafePath("sqlite.unsafe_mode")`), and existing `-wal`, `-shm` and `-journal` files follow the same two rules (review M-1, TM-03); a group- or world-*readable* file is accepted, because existing Nodestro files are 0644 (B-06-5), and `diagnose` reports its mode (TAB-SQ-003); (c) a new file is created with `O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW` and then `fchmod`ed to `file_mode`, so the umask never decides the mode (umask `0o277` still gives `0600`); SQLite gives `-wal`, `-shm` and `-journal` the main file's mode; (d) after `apply_profile` the path is checked again: no link among the four names, the same `(st_dev, st_ino)` as checked, and the name SQLite opened (`PRAGMA database_list`, whose unix VFS resolves links) equal to the real path; otherwise the connection is closed and `UnsafePath("sqlite.unsafe_path")` is raised, which also catches a link that was swapped in only while SQLite opened the file. `read_only` also sets `PRAGMA query_only=ON` (plan document 02 section 4.1 and 09 TAB-013) and only reads the journal mode; for a WAL database SQLite may create `-wal` and `-shm` (with the main file's mode), the database file stays byte-identical. The `FileNotFoundError` names no path |
| I-23 | 3.3 | `apply_profile` raises `UsageError("sqlite.transaction_open")`, reads the journal mode first and writes it only when it differs (idempotent without a write). `check_path` raises `UnsafePath("sqlite.symlink")`, logs the path at DEBUG on the logger `tabrion.sqlite`, and on Windows treats every reparse point (link or junction) as a link; links in parent directories are allowed, as in Orkexa. `loading_extensions` disables extension loading on every exit, even when it was enabled before the block: `sqlite3` has no getter for the setting, so the previous state cannot be restored (review I-3) |
| I-24 | 3.4 | `write_transaction` and `read_transaction` raise `UsageError("sqlite.not_autocommit")` when `isolation_level` is not `None` (Python 3.12 `autocommit=True` is accepted, `autocommit=False` refused). A `COMMIT` that fails while the transaction stays open (a deferred foreign key) is rolled back before the error propagates, so the connection is idle afterwards. A block that ended the transaction itself gets `UsageError("sqlite.transaction_ended")` instead of a failing `COMMIT` |
| I-25 | 3.4 | `read_transaction(enforce=True)` restores `query_only` to the value it had before (not unconditionally `OFF`), so a `connect(read_only=True)` connection stays `query_only`. A normal exit ends with `COMMIT` (a no-op for a read; with `enforce=False` a write is kept), an exception with `ROLLBACK` |
| I-26 | 3.5 | `migrate` reads `user_version` again after `BEGIN IMMEDIATE`. When another connection or process migrated in the meantime, only the steps still needed run (none at `target`, then `MigrationResult(target, target, backed_up)`), and a version above `target` raises `SchemaTooNew`; so two processes apply each step once (TAB-T-S30). A `BEGIN IMMEDIATE` that fails (busy, read-only file) passes the driver error through: no step ran. A step that ends the transaction itself raises `MigrationFailed` with `UsageError("sqlite.transaction_ended")` as `original`; what it committed cannot be undone. This includes `Connection.executescript`, which issues a `COMMIT` before it runs its script: a step that uses it leaves its statements committed while `user_version` is unchanged (review L-1). Steps must use `execute` (one statement each), never `commit`, `rollback` or `executescript`. `BackupFailed` and `MigrationFailed` are raised outside the `except` block, so they carry neither `__cause__` nor `__context__`. `target` must be an `int` from 0 to 2^31-1 (`UsageError("sqlite.invalid_version")`); the detail `missing` is the first missing version; the default `is_fresh` escapes the `_` of `sqlite_%` |
| I-27 | 3.6 | `backup` refuses any open transaction (`UsageError("sqlite.transaction_open")`): `sqlite3` cannot tell a read from a write transaction, and a copy taken inside the caller's write transaction would contain uncommitted data. A `":memory:"` target is `sqlite.invalid_target`, a non-`int` `pages_per_step` `sqlite.invalid_pages`. The four names of the target are checked for links. The temporary copy is created with `O_CREAT\|O_EXCL\|O_NOFOLLOW` as 0600; after SQLite opened it by name, and before the first page is written, it must still be that file (no link, the same `(st_dev, st_ino)`, the name SQLite opened equal to its real path), and it is checked again when it is `fsync`ed before it is published; otherwise `UnsafePath` and the copy is removed (review M-3). Without `overwrite`, the finished file is published with `os.link`, so a target that appeared during the copy is never replaced (`FileExistsError`); on a file system without hard links it falls back to a check and `os.replace` |
| I-28 | 3.6 | The copy is switched to `journal_mode=DELETE` before it is closed, so the backup is one self-contained file; `connect` switches it back to WAL when it is used. `verify` runs `quick_check` on a separate `mode=ro&immutable=1` connection (it creates no side files); `sqlite.backup_corrupt` carries only `details={"pages": n}`. With `verify=False`, `BackupResult.quick_check` is `"not checked"`. On POSIX the directory is `fsync`ed after the rename |
| I-29 | 3.7 | `messages` is empty when `ok`. `truncated` asks SQLite for `max_errors + 1` rows. When the check itself fails with `SQLITE_CORRUPT` or `SQLITE_NOTADB`, the report is `ok=False` with the error's `safe_message` as its only message; any other error passes through. `max_errors` and `limit` must be an `int` from 1 to 2^31-2 (`UsageError("sqlite.invalid_limit")`); a `table` with a NUL character is `UsageError("sqlite.invalid_name")`. Results are plain values whatever the caller's `row_factory` or `text_factory`. An idle connection with driver-managed transactions (`isolation_level=""`, the `sqlite3` default) runs the checks as they are, without a read transaction of Tabrion's own: they are single read-only `PRAGMA` statements, which the driver runs without opening a transaction (review L-2); `diagnose(run_quick_check=True)` likewise |
| I-30 | 3.8 | `path` is the name SQLite opened (`PRAGMA database_list`); SQLite's unix VFS has already resolved links in it. `file_mode` is `None` on Windows, where POSIX modes say nothing. `is_symlink` covers the file and its `-wal`, `-shm` and `-journal` files. `secure_delete` is true only for `ON` (1), not for `FAST` (2). `compile_options` keeps `DEFAULT_FOREIGN_KEYS`, `DEFAULT_WAL_SYNCHRONOUS`, `ENABLE_DBSTAT_VTAB`, `ENABLE_FTS3/4/5`, `ENABLE_JSON1`, `ENABLE_LOAD_EXTENSION`, `ENABLE_MATH_FUNCTIONS`, `ENABLE_RTREE`, `OMIT_LOAD_EXTENSION`, `SECURE_DELETE`, `TEMP_STORE` and `THREADSAFE`. When the database directory is writable by others and has no sticky bit (POSIX, TM-03), `diagnose` logs a WARNING on the logger `tabrion.sqlite` that another user can replace the database file; when the group or others can write it otherwise, sticky or not, the WARNING says that other users can create files next to the database (side files, #4 re-review DL-1); neither names the path (the path at DEBUG): the frozen `SQLiteDiagnosis` has no field for it, the field is TB-043 (review M-2, deviation D-40) |
| I-31 | Conventions | T-NOTX for SQLite (`tests/sqlite/test_notx.py`): every function that takes a connection leaves `in_transaction`, `isolation_level`, `row_factory`, `text_factory` and `query_only` as it found them, idle and inside the caller's transaction; the checks and `diagnose` read inside the caller's transaction or in a short `read_transaction` of their own |

## 4. `tabrion.postgres` (extra `postgres`, psycopg 3.2 or later)

Unless stated otherwise, every function takes a **`psycopg.Connection`**, with or without `autocommit` and with any
row factory. The functions read through their own cursor with `tuple_row`, as in Axentra `roles.py:74`. Read-only
functions leave the transaction status unchanged:

- If the connection ran without autocommit and no transaction was open, a `rollback()` follows, as in Axentra
  `database.py:353-356`.
- If a transaction was running, the functions run inside it, without commit or rollback.

### 4.1 `SessionSettings`, `configure`, `session_configurator`

```python
@dataclass(frozen=True, slots=True)
class SessionSettings:
    statement_timeout_ms: int | None = 30_000
    lock_timeout_ms: int | None = 10_000
    idle_in_transaction_timeout_ms: int | None = 300_000
    transaction_timeout_ms: int | None = None   # PG 17 or later only; skipped on older servers, diagnose reports it
    application_name: str | None = "tabrion"
    search_path: str | None = None          # one schema name, quoted as an identifier
    timezone: str | None = "UTC"

    @classmethod
    def unchanged(cls) -> SessionSettings   # all None: the existing code configures itself

def configure(connection: psycopg.Connection, settings: SessionSettings = SessionSettings()) -> None
def session_configurator(settings: SessionSettings) -> Callable[[psycopg.Connection], None]
```

- **`session_configurator`** returns a function usable directly as `psycopg_pool.ConnectionPool(configure=...)`. It
  calls `configure(conn, settings)`. The settings are validated **when it is created**, not per connection.

**Rules for pools of other owners (M10):**

1. Only the **pool owner** configures sessions. Axentra uses `session_configurator` for its **own** pool
   (`database.py:237-247`, today `_configure`). For `Database.on_pool(pool)` (a borrowed Nodestro pool) Axentra calls
   **nothing**; that is today's behavior (`database.py:249-268`: "Axentra sets nothing at session level") and stays.
2. Nodestro uses `session_configurator` for its own pool (`storage_postgres.py:255-264`) with exactly today's GUCs,
   so Axentra sees the same values on the shared pool as today.
3. Session settings are never security-relevant (R-SET); a wrong configuration cannot weaken RLS.
4. Test TAB-T-P16: a pool with `session_configurator` is used by two consumers (M10 simulation). The GUCs are
   identical, and a transaction-local `set_config('app.tenant_id', ..., true)` is empty after the connection is
   returned.

- **`configure`:** one round trip `SELECT set_config(%s, %s, false), ...`, only for fields that are not `None`.
  **`configure` never commits** (plan document 06 section 11). With autocommit the setting is permanent at once.
  Without autocommit the `SELECT` opens an implicit transaction; the caller commits it (as Axentra's `_configure`
  calls `conn.commit()` itself today), otherwise the setting is lost on a later rollback. The same applies inside a
  running transaction.
- **`session_configurator`** calls `commit()` after `configure` exactly when the connection was IDLE without
  autocommit before: the transaction was then opened by `configure` itself (contract rule), and `psycopg_pool`
  requires an IDLE connection after the callback. The callback runs only on the pool owner's connections (M10 rule 1);
  Axentra never calls it for `on_pool`.
- **`transaction_timeout_ms`:** set only when `connection.info.server_version >= 170000`. Older servers skip it
  without an error; `tabrion.check` then reports TAB-PG-012 (info).
- **Validation:** timeouts at least 0; `search_path` by the regex `[A-Za-z_][A-Za-z0-9_$]{0,62}`;
  `application_name` at most 63 ASCII characters. Otherwise `ConfigurationError`.
- **Errors:** driver errors pass through (layer A).
- **Existing code:** Nodestro `_configure` (`storage_postgres.py:275-286`) gives identical GUCs with
  `SessionSettings(statement_timeout_ms=..., idle_in_transaction_timeout_ms=..., lock_timeout_ms=None,
  application_name=None, timezone=None, search_path=schema)`. Axentra `_configure` (`database.py:273-275`) equals
  `SessionSettings(statement_timeout_ms=t, lock_timeout_ms=None, idle_in_transaction_timeout_ms=None,
  application_name=None, timezone=None)`.
- **Tests:** TAB-T-P01 (values through `SHOW`), P02 (no commit by `configure` without autocommit: status INTRANS,
  value gone after a rollback; `session_configurator` commits and leaves IDLE), P03 (inside an open transaction: no
  commit, setting active), P04 (validation), P05 (one round trip, counted through a psycopg tracer).

### 4.2 `ping`

```python
@dataclass(frozen=True, slots=True)
class PingResult:
    ok: bool
    latency_ms: float | None
    server_version: int | None        # server_version_num
    in_recovery: bool | None
    classification: Classification | None   # when ok=False

class _PoolLike(Protocol):
    def connection(self, timeout: float | None = ...) -> AbstractContextManager[psycopg.Connection]: ...

def ping(source: psycopg.Connection | _PoolLike, *, timeout: float = 5.0) -> PingResult
```

- **Steps:** `SELECT current_setting('server_version_num')::int, pg_is_in_recovery()` with
  `SET LOCAL statement_timeout` = `timeout` in a short transaction of its own. A pool source lends the connection
  with `timeout`.
- **Errors:** **never** raises for database errors (gives `ok=False` with a classification), only for programming
  errors (`TypeError`).
- **Existing code:** Axentra `ping` (`database.py:348-357`) returns only `bool`; Tabrion adds latency and version.
  Nodestro `DatabaseHealth` (`nodestro-api server_app.py:400-455`) stays as a wrapper (thread, one check at a time)
  and will call `ping`.
- **Tests:** TAB-T-P06..P09 (ok, server stopped, timeout with `pg_sleep`, pool source).

### 4.3 `diagnose`

```python
@dataclass(frozen=True, slots=True)
class PostgresDiagnosis:
    server_version: int
    server_version_text: str
    database: str
    user: str
    schema: str | None
    search_path: str
    in_recovery: bool
    ssl: bool | None                  # pg_stat_ssl for the own backend PID; None without the right
    application_name: str
    statement_timeout_ms: int
    lock_timeout_ms: int
    idle_in_transaction_timeout_ms: int
    archive_mode: str | None          # None without the right
    wal_level: str | None
    role: RoleInfo
    host_is_local: bool               # from connection.info.host

def diagnose(connection: psycopg.Connection) -> PostgresDiagnosis
```

Read-only; fields without read rights are `None`. The basis for the TAB-PG-* findings. Tests TAB-T-P10..P12.

### 4.4 `extension_status`

```python
@dataclass(frozen=True, slots=True)
class ExtensionStatus:
    name: str
    state: Literal["active", "other_schema", "installable", "unavailable"]
    installed_version: str | None
    available_version: str | None
    extension_schema: str | None
    schema: str | None                # current_schema()
    command: str | None               # SQL that fixes the state (superuser or owner); None if nothing to do

def extension_status(connection: psycopg.Connection, name: str, *, probe_type: str | None = None) -> ExtensionStatus
```

- **States:**
  - `active`: installed and `extnamespace` is in `current_schemas(true)`. With `probe_type` set (for example
    `"vector"`), `to_regtype(probe_type) IS NOT NULL` must also hold; exactly the Nodestro semantics.
  - `other_schema`: installed, schema not visible; `command = ALTER EXTENSION ... SET SCHEMA "<schema>";`.
  - `installable`: in `pg_available_extensions`; `command = CREATE EXTENSION ... SCHEMA "<schema>";`.
  - `unavailable`: neither installed nor available.
- **Validation:** `name` by the regex `[a-z_][a-z0-9_]*`; identifiers are quoted.
- **Compared with Nodestro `pgvector_status` (`storage_postgres.py:907-968`):** the same four states. Nodestro calls
  `extension_status(conn, "vector", probe_type="vector")` and gets the same check as `to_regtype('vector')`
  (TAB-T-P15 proves equality for all four states). **The message texts stay product text in Nodestro.**
- **Tests:** TAB-T-P13..P15 against the images `pgvector/pgvector:pg17` and `postgres:17`.

### 4.5 `RoleInfo`, `role_info`, `check_role`

```python
@dataclass(frozen=True, slots=True)
class RoleInfo:
    role: str
    superuser: bool
    bypass_rls: bool
    table_owner: bool

    @property
    def restricted(self) -> bool: ...   # not (superuser or bypass_rls or table_owner)

def role_info(connection: psycopg.Connection, *,
              owner_scope: Literal["schema", "rls"] = "schema") -> RoleInfo

def check_role(connection: psycopg.Connection, *, allow_superuser: bool = False, allow_table_owner: bool = False,
               owner_scope: Literal["schema", "rls"] = "schema") -> RoleInfo      # raises RoleUnsafe
```

- **Semantics:**
  - `table_owner` holds when the role owns a table (`relkind in ('r','p')`) in scope, by
    `pg_has_role(current_user, relowner, 'MEMBER')`.
  - `owner_scope="schema"`: every table in `current_schema()`; equals Nodestro (`storage_postgres.py:679-689`).
  - `owner_scope="rls"`: only tables with `relrowsecurity` in `current_schemas(false)`; equals Axentra
    (`database.py:359-382`). **Axentra is a consumer** and uses `owner_scope="rls"` in `Axentra.start()`.
- **`check_role`:** the error logic follows Axentra `database.py:384-405`, with `allow_superuser` and
  `allow_table_owner` (allowing superuser implies allowing table ownership). `RoleUnsafe.details["reasons"]` takes
  the values `superuser`, `bypass_rls`, `table_owner`. The message is English and neutral; Axentra maps it to its
  `ConfigurationError` texts (plan document 06).
- **Tests:** TAB-T-R01..R08 (per flag, both `owner_scope` values, a table with and without `relrowsecurity`).

### 4.6 `RoleReport`, `inspect_role`, `grant_runtime_role`

```python
@dataclass(frozen=True, slots=True)
class RoleReport:
    role: str
    owner: str                     # current_user of the owner connection
    schema: str
    database: str
    tables: int
    connect: bool
    superuser: bool
    bypass_rls: bool
    owned_tables: tuple[str, ...]
    can_create: bool
    @property
    def restricted(self) -> bool: ...
    def as_dict(self) -> dict[str, object]: ...   # keys = Nodestro dict united with Axentra as_dict

def inspect_role(connection: psycopg.Connection, role: str, *, schema: str | None = None) -> RoleReport
    # raises RoleMissing

def grant_runtime_role(connection: psycopg.Connection, role: str, *, schema: str | None = None,
                       protected_tables: Sequence[str] = (), grant_connect: bool = True) -> RoleReport
    # raises RoleMissing, RoleUnsafe
```

- **`inspect_role`** unites Nodestro `inspect_role` (`storage_postgres.py:867-904`, with `database`) and Axentra
  `inspect_role` (`roles.py:72-104`, with the `schema` parameter); both are consumers. `as_dict()` returns every key
  of both formats: `role, owner, schema, database, tables, superuser, bypass_rls, owned_tables (list), connect,
  can_create, restricted`. Read-only.
- **`grant_runtime_role`:**
  - Pre-check by `inspect_role`. `role == owner`, superuser, `BYPASSRLS` or `owned_tables` raise `RoleUnsafe`, and
    nothing is granted.
  - In **one** transaction (`connection.transaction()`, works with and without autocommit):
    1. `GRANT USAGE ON SCHEMA`
    2. `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES`
    3. `GRANT USAGE, SELECT ON ALL SEQUENCES`
    4. `GRANT EXECUTE ON ALL FUNCTIONS`
    5. `ALTER DEFAULT PRIVILEGES` for tables, sequences and functions
    6. For every existing table in `protected_tables` (schema-qualified through `to_regclass`):
       `REVOKE INSERT, UPDATE, DELETE, TRUNCATE`.
  - Then, when `grant_connect` holds and `connect=False`: `GRANT CONNECT ON DATABASE` in a transaction of its own.
    `InsufficientPrivilege` is ignored there; the result then reports `connect=False`.
  - Returns a fresh `inspect_role`. The function is idempotent.
- **Drop-in:** Nodestro `grant_runtime_role(owner, role)` (`storage_postgres.py:808-864`) becomes
  `protected_tables=("nodestro_schema_migrations", "axentra_schema_migrations")`. Axentra
  `grant_runtime_role(url_or_conn, role, schema=)` (`roles.py:107-190`) becomes
  `protected_tables=("axentra_schema_migrations",)`; the URL variant stays a thin Axentra wrapper, as does the mapping
  of `RoleUnsafe`/`RoleMissing` to `ConfigurationError` with today's texts.
- **Tests:** TAB-T-R09..R10 (inspect); TAB-T-R11..R18 (grant): the catalog snapshot (`pg_namespace.nspacl`,
  `information_schema.role_table_grants`, `pg_default_acl`, `role_routine_grants`, `has_database_privilege`) equals
  the checked-in golden files of the Nodestro and Axentra behavior (TAB-022) on a fresh schema; idempotency; refusal
  cases; rights on the bookkeeping tables revoked; an owner role without the right to grant CONNECT.

### 4.7 `SessionLock`, `lock_key`, advisory helpers (from 0.1)

```python
def lock_key(name: str) -> int    # stable int64 from SHA-256(name)[:8], signed big-endian

class SessionLock:
    def __init__(self, conninfo: str, *, connect_kwargs: Mapping[str, Any] | None = None,
                 configure: Callable[[psycopg.Connection], None] | None = None) -> None
    def try_acquire(self, key: int) -> bool
    def acquire(self, key: int, *, timeout: float | None = None, poll_interval: float = 0.2) -> bool
    def holds(self, key: int) -> bool
    def release(self, key: int) -> None
    def held_keys(self) -> frozenset[int]
    def close(self) -> None
    def __enter__(self) -> SessionLock
    def __exit__(self, *exc: object) -> None
```

**Semantics** (unites Nodestro `storage_postgres.py:418-481` and Axentra `_wait_for_lock` `migrations.py:309-318`;
both are consumers):

- An own, **unpooled** connection: `psycopg.connect(conninfo, autocommit=True, connect_timeout=10, **connect_kwargs)`,
  opened lazily. Then `configure(conn)`; by default
  `tabrion.postgres.configure(conn, SessionSettings(application_name="tabrion-lock"))`. Nodestro passes its own
  `_configure` (drop-in for `storage_postgres.py:418-440`).
- Holding a key several times is counted; `pg_advisory_unlock` follows only at the last release.
- Before every operation, `SELECT 1` probes liveness. If the connection is dead, it is replaced and **every count is
  cleared**: the locks are gone and `holds()` returns `False`.
- `acquire` polls `pg_try_advisory_lock` instead of waiting blocked. Reason as in Axentra: no open statement and no
  snapshot while waiting, compatible with `CREATE INDEX CONCURRENTLY`. After `timeout` it returns `False`.
- Database errors while connecting pass through (psycopg). After `close()` every operation raises
  `UsageError("postgres.lock_closed")`.
- Thread-safe through an internal `threading.Lock`. **Not** fork-safe: after `os.fork()` the child opens its own
  connection, detected by comparing the PID.
- Keys are int64; the existing constants (Nodestro `0x6E6F646573747230`, `0x6E6F64657374726F`; Axentra
  `0x617865_6E747261`) stay valid.

**Advisory locks on a given connection** (Axentra runner `migrations.py:309-318`, Axentra worker). The Nodestro
migration keeps using `pg_advisory_xact_lock` in its own transaction.

```python
def try_advisory_lock(connection: psycopg.Connection, key: int) -> bool
def wait_advisory_lock(connection: psycopg.Connection, key: int, *, poll_interval: float = 0.2,
                       timeout: float | None = None) -> bool
def advisory_unlock(connection: psycopg.Connection, key: int) -> bool
```

- Session-level locks on the **given** connection; Tabrion neither opens nor closes it.
- **Polling semantics (exactly as Axentra):** `SELECT pg_try_advisory_lock(%s)`; on `False`,
  `time.sleep(poll_interval)` and again. Between attempts **no statement is active and no snapshot is open**. Reason
  from the Axentra docstring: a blocking `pg_advisory_lock` holds a statement and a snapshot, and
  `CREATE INDEX CONCURRENTLY` in another session waits for every open snapshot. That would deadlock two migration
  processes.
- If the connection is not in autocommit and IDLE, its own implicit transaction is rolled back after every attempt.
  While a transaction runs, it raises `UsageError("postgres.lock_in_transaction")`, because the snapshot would
  otherwise be held.
- `timeout=None` waits without limit (as Axentra today). With `timeout`, it returns `False` when it expires.
  `advisory_unlock` returns the result of `pg_advisory_unlock`.
- Tests: TAB-T-L11 two processes, one waits; L12 while waiting, `pg_stat_activity` shows the waiting connection with
  `state='idle'` and `backend_xmin IS NULL`; L13 `CREATE INDEX CONCURRENTLY` in a third session completes while one
  session holds and one waits (the Axentra case); L14 timeout; L15 a call inside an open transaction raises
  `UsageError`.

**Tests:** TAB-T-L01..L10 (two competing processes, counting, release, connection loss through
`pg_terminate_backend` gives `holds()=False`, polling timeout, no active statement while waiting per
`pg_stat_activity`, close, fork).

## 5. Layer B (`tabrion`, extra `sqlalchemy`)

### 5.1 `Profile`, `TLSMode`

```python
class TLSMode(enum.StrEnum): AUTO = "auto"; VERIFY_FULL = "verify-full"; REQUIRE = "require"; INSECURE = "insecure"

@dataclass(frozen=True, slots=True)
class Profile:
    sqlite: SQLiteProfile = SQLiteProfile()
    postgres: SessionSettings = SessionSettings()
    application_name: str = "tabrion"
    pool_size: int = 5
    max_overflow: int = 0
    pool_timeout: float = 10.0
    pool_pre_ping: bool = True
    pool_recycle: float = 1800.0
    connect_timeout: float = 10.0
    tls: TLSMode = TLSMode.AUTO
    max_rows: int = 10_000
    read_only: bool = False      # SQLite: open with mode=ro + query_only; PG: session
                                 # default_transaction_read_only=on, transaction() raises UsageError

    @classmethod
    def default(cls) -> Profile
    @classmethod
    def readonly(cls) -> Profile    # read_only=True; the Studio default
    @classmethod
    def edge(cls) -> Profile        # SQLite synchronous=NORMAL, secure_delete=False
    @classmethod
    def testing(cls) -> Profile     # statement 5 s, lock 2 s, pool 2
```

Studio mapping (checked against plan document 03): the Studio profile "sqlite_readonly" is `Profile.readonly()` with
a SQLite URL; the Studio profile "postgres" is `Profile.default()`, in read-only mode `Profile.readonly()`. Enforcement
happens **in the database** (`mode=ro`, or `default_transaction_read_only` plus `BEGIN READ ONLY`), not through SQL
text filters. It is a guard against accidents, not a security boundary; the hard boundary is a role without write
rights (implementation note I-56).

Stable (S). TLS rules per specification section 5.3; violations raise `ConfigurationError("config.tls")` in
`from_url`.

### 5.2 `Database`: creation and lifetime

```python
class Database:
    @classmethod
    def from_url(cls, url: str | sqlalchemy.URL, *, profile: Profile | None = None,
                 engine_options: Mapping[str, Any] | None = None) -> Database
    @classmethod
    def from_env(cls, name: str = "DATABASE_URL", *, profile: Profile | None = None,
                 engine_options: Mapping[str, Any] | None = None) -> Database
    @classmethod
    def from_engine(cls, engine: sqlalchemy.Engine, *, profile: Profile | None = None,
                    install_listeners: bool = True) -> Database
    dialect: Literal["sqlite", "postgresql"]      # property
    engine: sqlalchemy.Engine                      # property, controlled access (specification section 3.1)
    safe_url: str                                  # property
    closed: bool                                   # property
    def close(self) -> None                        # engine.dispose(); idempotent
    def __enter__(self) -> Database
    def __exit__(self, *exc: object) -> None
```

- **Supported URLs:** `sqlite:///path` or `sqlite+pysqlite:///`, `sqlite://` for memory; `postgresql://...`,
  `postgresql+psycopg://...` (`postgresql://` is rewritten to `+psycopg`). `postgresql+psycopg2` and other drivers
  raise `ConfigurationError("config.driver")`.
- **SQLite path:** `check_path` and the 0600 creation run in `from_url` (before the first connect). `sqlite://`
  (memory) uses `StaticPool` so every thread sees the same database; documented.
- **`from_env`:** through `tabrion.config.url_from_env` (supports `*_FILE`).
- **`from_engine`:** a foreign engine; the listeners are added. `close()` does **not** dispose it; the engine belongs
  to the caller.
- **Errors:** `ConfigurationError` (URL, TLS, driver); `UnsafePath`; after `close()` every operation raises
  `UsageError("database.closed")`.
- **Thread safety:** yes.
- **Tests:** TAB-T-B01..B06.

### 5.3 Transactions

```python
Statement = str | sqlalchemy.sql.Executable
Params = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None   # a sequence means executemany

class Database:
    @contextmanager
    def transaction(self, *, timeout: float | None = None,
                    isolation: Literal["read_committed", "repeatable_read", "serializable"] | None = None
                    ) -> Iterator[Transaction]
    @contextmanager
    def read(self, *, timeout: float | None = None) -> Iterator[Transaction]

class Transaction:
    read_only: bool
    connection: sqlalchemy.Connection            # controlled access
    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int
    def fetch_one(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
                  timeout: float | None = None) -> sqlalchemy.Row[Any] | None
    def fetch_all(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
                  timeout: float | None = None) -> list[sqlalchemy.Row[Any]]
    def fetch_scalar(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
                     timeout: float | None = None) -> Any
    def query(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
              max_rows: int | None = None, timeout: float | None = None) -> Result          # 0.2
    def stream(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
               batch_size: int = 500, timeout: float | None = None
               ) -> Iterator[Sequence[sqlalchemy.Row[Any]]]   # 0.2; PG: server cursor, SQLite: fetchmany
    def cancel(self) -> None                     # 0.2, from another thread; Studio "ExecutionHandle.cancel()"
    @contextmanager
    def savepoint(self) -> Iterator[None]        # 0.2, X
```

| Method | SQLite | PostgreSQL |
|---|---|---|
| `transaction()` | `BEGIN IMMEDIATE` through the `begin` event; commit on success, rollback on an exception | `BEGIN [ISOLATION LEVEL ...]` (SQLAlchemy `isolation_level` option) |
| `read()` | `PRAGMA query_only=ON` plus `BEGIN`; OFF afterwards | SQLAlchemy `execution_options(postgresql_readonly=True)` gives `BEGIN READ ONLY` |
| `timeout` | Deadline through `set_progress_handler` (every 1 000 VM steps); expiry raises `StatementTimeout` | `SET LOCAL statement_timeout = <ms>` before the statement; it lasts for the rest of the transaction, so the profile value is restored before the next statement without a timeout |
| `execute` | `rowcount` (-1 becomes 0 for DDL); sequence parameters give `executemany` | same |
| `fetch_one` | 0 rows give `None`; more than 1 raises `MultipleRowsError` | same |
| `fetch_scalar` | first column of the only row; 0 rows give `None`; more than 1 raises `MultipleRowsError` | same |
| Write in `read()` | `ReadOnlyViolation` | `ReadOnlyViolation` (25006) |
| `cancel()` | `sqlite3.Connection.interrupt()` | `psycopg.Connection.cancel_safe()` (psycopg 3.2 or later) |

**Further rules:**

- **Text statements:** a `str` becomes `sqlalchemy.text()` with named parameters `:name`.
- **Nesting:** a `transaction()`/`read()` in the same thread inside an open Tabrion transaction of the same
  `Database` raises `UsageError("database.nested_transaction")`. The database keeps a thread-local marker for this.
- **Errors:** driver errors raise the matching `DatabaseError` subclass (section 1.4), `raise ... from None`, with
  `original` and the shortened `statement`. An exception ends the transaction with a rollback. A commit error uses
  `during_commit=True`, so `outcome_unknown` for CONNECTION.
- **Autonomous short forms on `Database`:** `execute`, `fetch_one`, `fetch_all`, `fetch_scalar` and `query` with the
  same signatures. `execute` runs in `transaction()`, the others in `read()`.

**Tests:** TAB-T-B07 lost update in layer B, 5 threads, result 5 on SQLite, and 5 on PG with `SELECT ... FOR UPDATE`;
B08..B10 rollback, exceptions, nesting; B11..B12 read-only on both engines; B13..B14 timeout on both engines with
`pg_sleep` or a recursive CTE; B15 `cancel` from a second thread; B16 `executemany`; B17 `MultipleRowsError`; B18
parameter leak (the value `SECRET-4711` in no error); B19 `ResourceWarning` on a forgotten `close`; B20 SQLAlchemy Core
expressions (`select(table)`); B21 connection return after an exception (pool statistics); B22 `query_only` does not
stick after an error (connection reused, writing possible).

### 5.4 `Result`, `Column` (from 0.2)

```python
@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type_name: str | None  # PG: type name through an OID lookup; SQLite: declared type or None
    nullable: bool | None


@dataclass(frozen=True, slots=True)
class Result:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int  # rows delivered
    truncated: bool  # more rows exist than max_rows
    elapsed_ms: float
    statement_kind: Literal["select", "dml", "ddl", "other"]
```

`max_rows` defaults to `Profile.max_rows`. It reads with `fetchmany(max_rows + 1)` and cuts, without a server cursor;
a named cursor for PG is possible from 1.x. Values stay Python values; Studio serializes. Tests TAB-T-B26..B30.

### 5.5 Health, diagnosis, inspection, schema gate

```python
@dataclass(frozen=True, slots=True)
class Health:
    ok: bool
    dialect: str
    latency_ms: float | None
    server_version: str | None
    pool: Mapping[str, int] | None   # checked_out, size, overflow (SQLAlchemy pool.status)
    classification: Classification | None

class Database:
    def health(self, *, timeout: float = 5.0, deep: bool = False) -> Health   # never raises for database errors
    def check(self) -> list[Finding]                               # tabrion.check.check(self)
    @contextmanager
    def inspector(self) -> Iterator[sqlalchemy.Inspector]          # 0.2; on a read-only connection
    def on_begin(self, callback: Callable[[sqlalchemy.Connection, bool], None]) -> Callable[[], None]
        # 0.2, X; bool = read_only; returns an unregister function

@dataclass(frozen=True, slots=True)
class SchemaStatus:
    current: tuple[str, ...]      # revisions in the database (alembic_version); empty = unversioned
    heads: tuple[str, ...]        # target heads of the script directory
    pending: tuple[str, ...]      # revisions to apply, in order
    is_current: bool
    unknown: tuple[str, ...]      # database revisions the script directory does not know (newer version)

# module tabrion.migrations (extra migrations)
def status(database: Database, *, script_location: str | os.PathLike[str],
           version_table: str = "alembic_version", version_table_schema: str | None = None) -> SchemaStatus
def require_current(database: Database, **kw: Any) -> SchemaStatus            # raises SchemaNotCurrent
```

- **`health`** uses `tabrion.postgres.ping` on a pool connection for PG; for SQLite `SELECT 1` plus
  `PRAGMA quick_check(1)` only with `deep=True`. There is no second pool. `deep` is keyword-only and off by default,
  so a readiness probe stays cheap; on PostgreSQL it changes nothing in 0.1 (G-9).
- **`tabrion.migrations.status`** runs read-only through `alembic.script.ScriptDirectory(script_location)` and
  `alembic.runtime.migration.MigrationContext.configure(connection, opts={"version_table": ...})` on a `read()`
  connection. **`env.py` is not run**, so no application code and no side effects; Studio needs exactly that. It
  never migrates; migrating stays `alembic upgrade`.
- **Tests:** TAB-T-B23..B25, TAB-T-M01..M08 (fresh, current, pending, unknown newer revision, several heads, missing
  Alembic gives the ImportError hint).

### 5.6 Experimental: `run_transaction`

```python
def run_transaction(self, fn: Callable[[Transaction], T], *, attempts: int = 3,
                    isolation: Literal["read_committed", "repeatable_read", "serializable"] | None = None,
                    backoff: float = 0.05) -> T
```

Retries only when `classification.retryable and not classification.outcome_unknown`, with exponential backoff and
jitter. Tests TAB-T-B31..B33, among them: a commit abort through the proxy is **not** retried.

### 5.7 Implementation notes (TAB-018)

Points the layer-B implementation settled where sections 5.1 to 5.5 leave room. None changes a signature or a name of
`surface.json`; deviations from the specification are recorded in the deviation log of
[STATUS.md](../program/STATUS.md#deviation-log) (D-52, D-53). Every `UsageError` and `ConfigurationError` carries only
its code and value-free details. Tests: `tests/unit/test_database.py` (SQLite, URLs, TLS, profiles),
`tests/postgres/test_database.py`, mutation probes `tests/unit/test_mutation_probes_layer_b.py`.

| # | Section | Note |
|---|---|---|
| I-37 | 5.1, 5.2 | **Profiles and creation.** `Profile.__post_init__` validates every field and raises `ConfigurationError("config.invalid_profile", details={"field": ...})`: `pool_size >= 1`, `max_overflow >= 0`, `max_rows >= 1` (`int`, not `bool`), `pool_timeout`, `pool_recycle` and `connect_timeout` positive and finite, the booleans `bool`, `application_name` printable ASCII of at most 63 characters, `tls` a `TLSMode` (a string is converted). `Profile.testing()` also sets the SQLite busy timeout to 2 s (SQLite's lock timeout). `Profile.application_name` replaces the one in `Profile.postgres` (specification 4.3). `Database()` and `Transaction()` raise `TypeError` naming the factories (G-T1 F-14; through `__new__`, so `surface.json` is unchanged). `Statement`, `Params` and `Isolation` stay documentation-only aliases, not exported and not in `tabrion._sa.database.__all__` (G-T1 F-11, TODO TB-020 decided). `from_url`: an unparseable URL raises `ConfigurationError("config.url")` without its text; a dialect other than SQLite or PostgreSQL, or a driver other than `pysqlite`/`psycopg`, `config.driver`; a SQLite URL with a host, credentials or query parameters `config.url` (the creator would ignore them); a PostgreSQL URL without psycopg installed raises `ImportError` with the installation hint of `tabrion.postgres`. `engine_options` override the pool values of the profile; `creator`, `isolation_level`, `pool` and `poolclass` are Tabrion's (`ConfigurationError("config.engine_option", details={"option": ...})`), and so is `connect_args` for SQLite. A SQLite file is opened once by `tabrion.sqlite.connect` in `from_url` and closed again, which runs `check_path`, creates a new file with `file_mode` and checks the side files and the header (a missing file under `read_only` raises `FileNotFoundError`, a file that is no database `StorageCorrupt`); the engine then opens every connection through `tabrion.sqlite.connect` as its `creator` (D-52). `sqlite://` and `sqlite:///:memory:` use `StaticPool`, and Tabrion serializes its own transactions on the shared connection with a lock. `safe_url` is SQLAlchemy's rendering with the password hidden, passed through `tabrion.config.safe_url`, and `repr` uses it. `from_engine` accepts `pysqlite` and `psycopg` engines only (`config.driver`); its profile listener runs on checkout, once per DBAPI connection, so connections already in the pool get the profile too (a SQLite connection also gets `isolation_level = None`); `install_listeners=False` leaves out only that listener. The listener changes the session of every connection of that engine, the host's own checkouts included, and `close()` removes the listener but not what it set (PostgreSQL `statement_timeout`, `application_name`, ...; SQLite pragmas): use an engine of its own where the host must keep its settings. A read-only profile with `install_listeners=True` raises `ConfigurationError("config.invalid_profile", details={"field": "read_only"})`, because it would make the host's whole pool read-only, also after `close()` (T-NOTX rule 2, review M-2, deviation D-61); with `install_listeners=False`, `transaction()` and `execute` are still refused. The SQLite path checks, `file_mode` and the `0600` creation belong to `from_url` only: a foreign engine opens and creates its file itself (review L-2). `close()` removes Tabrion's listeners; it disposes only an engine Tabrion created. After `close()`, `engine`, `__enter__`, `health` and `check` raise `UsageError("database.closed")` as well. A `Database` collected unclosed emits `ResourceWarning` and is closed. The plan names only the ranges B01..B06 and B23..B25; here B01 is SQLite URLs, B02 PostgreSQL URLs and session settings, B03 TLS, B04 SQLite path checks, B05 `from_env`, B06 `from_engine` and the lifetime, B23 health on SQLite, B24 health on PostgreSQL and health that never raises, B25 `check` |
| I-38 | 5.3 | **Transactions.** A Tabrion transaction carries the execution option `tabrion_mode` (`write` or `read`); on SQLite the `begin` listener sends `BEGIN IMMEDIATE` for it, or `query_only` and `BEGIN`; a plain `db.engine.connect()` transaction gets a deferred `BEGIN` when the driver would not begin one itself, so it stays one transaction (specification 3.1). The nesting marker is thread-local per `Database`; another `Database`, and another thread, may open a transaction meanwhile. Under `Profile.read_only`, `transaction()` and `execute` raise `UsageError("database.read_only")` on both dialects. A `Transaction` used from another thread raises `UsageError("database.wrong_thread")`, after its block `UsageError("database.transaction_ended")`. `isolation` is accepted on SQLite and changes nothing (SQLite transactions are serializable); a value outside the three raises `ValueError`. `transaction(timeout=...)`/`read(timeout=...)` is the default timeout of every statement in the transaction; a statement's own `timeout` takes precedence. `timeout` must be a positive, finite number (`TypeError`, `ValueError`). `execute` with an empty sequence returns 0 without running the statement (SQLAlchemy would run it once without parameters). Parameters that are not a mapping (or, for `execute`, a sequence of mappings; `str` and `bytes` are no sequence) raise `TypeError`. `fetch_*` of a statement that returns no rows raises `UsageError("database.no_rows_returned")`; `MultipleRowsError` has the code `database.multiple_rows` |
| I-39 | 5.3 | **Errors.** A driver error becomes `_error_class(kind)` with the code `database.<kind>` (`database.unique`, `database.timeout`, ...), `original` the driver error (`.orig` of SQLAlchemy's wrapper), `statement` the SQL SQLAlchemy sent, cut to 200 characters, without parameters. It is raised outside any `except` block, so it has neither `__cause__` nor `__context__`; only an error of the caller's own use of `Transaction.connection`, translated in the context manager's `__exit__`, has the caller's error as a suppressed `__context__` (Python sets it there; `raise ... from None`). A checkout error is classified with `sent=False`, a `COMMIT` error with `during_commit=True` (then a rollback). A failed rollback invalidates the connection. A pool that has no connection within `pool_timeout` raises `ConnectionFailed` with kind `RESOURCE`, retryable, outcome known. A `sqlalchemy.exc.StatementError` that did not come from the driver (a missing bind parameter) raises `UsageError("database.invalid_statement")` with `original`. Exceptions of the caller pass unchanged, after the rollback |
| I-40 | 5.3 | **Timeouts.** SQLite: a deadline checked every 1 000 VM steps (`set_progress_handler`) for the statement and its fetch, removed afterwards; SQLite reports `SQLITE_INTERRUPT`, which becomes `StatementTimeout` with kind `TIMEOUT` (`sqlite_code` stays `SQLITE_INTERRUPT`). A wait for a lock is no VM work: it is bounded by the busy timeout, not by `timeout`. PostgreSQL: `SET LOCAL statement_timeout = <ms>` (milliseconds rounded up, at least 1), not `set_config`, so it takes no snapshot (as I-14); before the first one, `SHOW statement_timeout` keeps the value in force, and a later statement without a timeout gets that value back with `SET LOCAL`. Both end with the transaction |
| I-41 | 5.1 | **TLS** (specification 5.3). The hosts are the URL host, every `host` query entry (multi-host, with or without port) and `hostaddr`; `connect_args` override the query. Without a host, libpq's `PGHOST` counts, and without a `hostaddr`, `PGHOSTADDR` (libpq connects to it whatever the host is), then the Unix socket. A `service` (query or `connect_args`) or `PGSERVICE` is never local: `pg_service.conf` decides the host, which Tabrion does not read (review M-3). Local means every host is local by the rule of I-17 (socket, `localhost`, loopback). `auto`: local stays unchanged; otherwise a missing `sslmode` becomes `require` (in the URL, so `safe_url` shows it) and `disable`, `allow` or `prefer` raise `ConfigurationError("config.tls")`. `require` applies the same to every host. `verify-full` sets `verify-full` when it is missing and refuses every other value. `insecure` checks nothing. Two different `sslmode` values raise `config.tls`. `connect_timeout` is the profile's value rounded up to whole seconds, unless the URL or `connect_args` give one |
| I-42 | 5.1, 5.3 | **PostgreSQL read-only state.** The `connect` listener runs `session_configurator` (with `Profile.application_name`) and, under `Profile.read_only`, `set_config('default_transaction_read_only', 'on', false)`, then commits the transaction it opened. SQLAlchemy resets `postgresql_readonly` after `read()` by setting psycopg's `read_only = False`, which sends `BEGIN READ WRITE` from then on and overrides `default_transaction_read_only`. A `checkin` listener therefore gives `read_only` back to `None` (the session default) after a Tabrion `read()`, and invalidates the connection if it cannot (D-53; `test_b12_read_only_profile`) |
| I-56 | 5.1, 5.3 | **What read-only guarantees** (review H-1; amends "Enforcement happens in the database" in 5.1). `read()` and `Profile.read_only` stop writes from code that does not try to undo them: the database refuses them (`query_only`, `mode=ro`, `BEGIN READ ONLY`, `default_transaction_read_only`). They are a guard against accidents, **not a security boundary**: SQL that the caller runs can end read-only. Right before the `COMMIT` of a `read()`, Tabrion therefore reads the state again (PostgreSQL `transaction_read_only`, and `default_transaction_read_only` under `Profile.read_only`; SQLite `query_only` under a writable profile) and, when the caller's SQL lifted it (`SET TRANSACTION READ WRITE`, `SET transaction_read_only = off`, `SET default_transaction_read_only = off`, `PRAGMA query_only = OFF`), rolls back and raises `UsageError("database.read_only")`, so none of it is committed and no session default stays off on the pooled connection (one round trip per `read()`; an aborted transaction is not read). SQL that ends the transaction itself and commits another one (`ROLLBACK; BEGIN READ WRITE; ...; COMMIT`, or `SET ...; COMMIT`) is not stopped and can leave `default_transaction_read_only = off` on the connection, so `Database.engine` is not read-only after it. Under `Profile.readonly()` on SQLite, `mode=ro` holds whatever the SQL does. The only hard boundary is a role (or file permission) without write rights (plan 03:245-247, SR-03). `ATTACH` of a new file inside `read()` creates an empty file with the process umask (not `0600`); nothing can be written into it (`query_only` covers every schema; review L-1, TODO TB-046). **Session settings (TM-08, review M-1):** a `SET` without `LOCAL` in a Tabrion transaction (`search_path`, `statement_timeout`, `work_mem`, ...) is committed with it and stays on the pooled connection for every later checkout, because Tabrion runs no `RESET` or `DISCARD` (plan 04); use `SET LOCAL` or `set_config(..., true)`. Tabrion's own settings do not leak (I-40, I-42) |
| I-43 | 1.3 | **`classify` of a `TabrionError`** (TODO TB-040, review N-4). With `during_commit=True`, a stored classification of kind `CONNECTION` becomes `outcome_unknown` and not retryable (R-NORETRY), whatever `sent` it was built with; `sent` is not applied to a stored classification, because its default `True` cannot tell a deliberate call from a plain one. Every other stored classification is returned unchanged |
| I-44 | 5.5 | **Health.** SQLite: `SELECT 1` under the deadline of I-40 (latency of that statement), with `deep=True` also `PRAGMA quick_check(1)`; a result other than `ok` gives `ok=False` with kind `CORRUPT`. PostgreSQL: `tabrion.postgres.ping` on a connection of the engine's pool (no second pool); a failed checkout is classified with `sent=False`; `server_version` is `server_version_num` as text. `pool` is `checked_out`, `size` and `overflow` (connections beyond `pool_size`, never negative) for a `QueuePool`, `None` for `StaticPool`. A busy in-memory database within `timeout` gives `ok=False` with kind `TIMEOUT`. A wait for a pool connection is bounded by `pool_timeout`, not by `timeout` (TODO TB-056). `timeout` and `deep` are checked (`TypeError`, `ValueError`). `check()` delegates to `tabrion.check.check(self)`, which raises `NotImplementedError` until TAB-017 |

## 6. `tabrion.testing` (extra `testing`)

Activation is explicit: `pytest_plugins = ["tabrion.testing"]` in `conftest.py`. There is no `pytest11` autoload, so
the fixtures do not appear unasked in other test runs.

```python
def configure(*, env_prefix: str = "TABRION",
              postgres_env: Sequence[str] | None = None,    # default: (f"{env_prefix}_TEST_POSTGRES",)
              require_env: Sequence[str] | None = None,     # default: (f"{env_prefix}_REQUIRE_POSTGRES",)
              runtime_role_env: Sequence[str] | None = None, # default: (f"{env_prefix}_TEST_RUNTIME_ROLE",)
              require_extensions_env: Sequence[str] | None = None,  # default: (f"{env_prefix}_REQUIRE_EXTENSIONS",)
              schema_prefix: str = "t_") -> None
```

Called in `conftest.py` before the fixtures are used. It allows the existing names, so CI workflows stay unchanged:
`configure(env_prefix="NODESTRO")` gives `NODESTRO_TEST_POSTGRES`/`NODESTRO_REQUIRE_POSTGRES`;
`configure(env_prefix="ORKEXA")` gives `ORKEXA_TEST_POSTGRES`/`ORKEXA_REQUIRE_*`; Axentra uses
`configure(postgres_env=("AXENTRA_TEST_DATABASE_URL",), require_env=("AXENTRA_REQUIRE_POSTGRES",))`.

| Fixture / function | Scope | Returns | Behavior | Model |
|---|---|---|---|---|
| `tabrion_sqlite_path` | function | `Path` (not created) in `tmp_path` | - | Nodestro (about 29 files inline) |
| `tabrion_sqlite` | function | `sqlite3.Connection` through `tabrion.sqlite.connect` | closed afterwards | - |
| `tabrion_postgres_url` | session | admin or owner URL from the environment | environment missing: `skip`; require variable `1`: error | all three |
| `tabrion_postgres_schema` | function | `PostgresSchema(name, owner_url, runtime_url \| None)` | `CREATE SCHEMA t_<hex12>`; URL with `options=-csearch_path=...`; afterwards `DROP SCHEMA ... CASCADE`. In runtime-role mode also a LOGIN role `t_rt_<hex>` (random password, NOSUPERUSER, NOBYPASSRLS) plus `grant_runtime_role`; the role is dropped afterwards | Orkexa `support.py`, Nodestro `postgres_support.py` |
| `tabrion_role_setup` | session | `RoleSetup \| None`, default `RoleSetup()` | the roles of `tabrion_postgres_database`; a consumer overrides this fixture in its `conftest.py` (G-10) | Axentra `tests/conftest.py` |
| `tabrion_postgres_database` | session | `PostgresDatabaseHandle(url, name, owner_url, app_url)` | `CREATE DATABASE t_<hex>` (the admin needs CREATEDB); at the end `DROP DATABASE ... WITH (FORCE)`. The roles come from the fixture `tabrion_role_setup` (`RoleSetup \| None`, default `RoleSetup()`), with `RoleSetup(app: str \| None = None, owner: str \| None = None, split: bool = True)`; roles are `NOSUPERUSER NOBYPASSRLS`, plus `REVOKE CREATE ON SCHEMA public FROM PUBLIC` in the throwaway database. With `split=True`, two LOGIN roles **without superuser**: owner `t_own_<hex>` (database owner, NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE) and app `t_app_<hex>` (CONNECT; rights through `grant_runtime_role` after the consumer's migrations) | Axentra `tests/conftest.py` |
| `tabrion_postgres_roles` | session | `TestRoles(owner, app, owner_url, app_url)` | the same role setup in an existing database | Axentra |
| `truncate_tables(target, *, schema=None, like=None, exclude=())` | function | `int` (number of tables) | `target` is a connection or URL; `TRUNCATE ... RESTART IDENTITY CASCADE` of every table of the schema (optional name pattern `like`) in **one** statement; `exclude`, for example bookkeeping tables; identifiers quoted | Axentra (truncate between tests) |
| `require_extension(conn, name)` | function | `ExtensionStatus` | not active: try `CREATE EXTENSION`, otherwise `skip`; `name` in the require variable: error | Orkexa `ORKEXA_REQUIRE_*` |
| `postgres_server_version` | session | `int` | - | - |
| `profiles.sqlite_profile_cases()` | function | `list[ProfileCase]` | checks FK, WAL, busy timeout, permissions, symlinks, IMMEDIATE for **any** SQLite connect function | - |
| `profiles.portability_cases()` | function | `list[PortabilityCase]` | data type rules (specification section 6) on SQLite and PostgreSQL | - |

The case types (G-3, revised by G-T1 condition C-2):

```python
SQLiteConnect = Callable[[Path], sqlite3.Connection]  # the application's connect function for a given path


@dataclass(frozen=True, slots=True)
class ProfileCase:
    id: str
    title: str
    check: Callable[[SQLiteConnect, Path], None]  # (connect, empty directory); the case picks the file path


@dataclass(frozen=True, slots=True)
class PortabilityCase:
    id: str
    title: str
    check: Callable[[Database], None]  # an open Database on an empty schema, either engine
```

`check` raises `AssertionError` on a violation. A profile case needs the path, because it tests the mode of a *new*
file and the refusal of symlinks for the main file, `-wal`, `-shm` and `-journal`. A portability case takes a layer-B
`Database`, because the rules must hold on both engines; `tabrion.testing` is the top layer and may use layer B.

How a consumer passes its roles (G-10, condition C-4):

```python
# conftest.py
pytest_plugins = ["tabrion.testing"]


@pytest.fixture(scope="session")
def tabrion_role_setup() -> RoleSetup | None:
    return RoleSetup(app="axentra_app", owner="axentra_owner")
```

**Tests:** TAB-T-X01..X18. Tabrion's own suite uses only these fixtures (dogfooding). It also checks that no schemas
or roles with the prefix `t_` remain after each test.

## 7. `tabrion.check`

```python
class Severity(enum.StrEnum): INFO = "info"; WARNING = "warning"; ERROR = "error"

@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    title: str
    detail: str
    remedy: str

def check(target: sqlite3.Connection | psycopg.Connection | Database, *,
          expect: Literal["runtime", "owner", "any"] = "runtime") -> list[Finding]
```

- The codes are in specification section 9.
- `expect="owner"` suppresses TAB-PG-003 and TAB-PG-009.
- **Dispatch (G-T1 condition C-1, fix B; ADR 0012).** `tabrion.check` stays standard-library only and never imports
  `tabrion.postgres` or layer B, also not inside a function (import-linter records function-level imports, so the
  lazy import planned in doc 05 section 2.3 would break the core and the layers contract). It checks a
  `sqlite3.Connection` itself. For any other target it looks up the module of the target's type (or of a base class)
  in a fixed table: `psycopg` leads to `tabrion.postgres._check`, `tabrion._sa.database` to `tabrion._sa._check`. It
  loads that collector with `importlib.import_module` and calls `collect(target, expect=expect)`. Any other type
  raises `TypeError`. The collectors may import `tabrion.check` (for `Finding`); `tabrion._sa._check` may use
  `tabrion.postgres._check`. A test checks that `tabrion.check` has no such import and that the table names only
  these two modules.
- Read-only; never raises for missing rights (gives an `info` finding).
- Tests TAB-T-K01..K30, a positive and a negative case per code.

## 8. Compatibility table: existing code to Tabrion

Every row is a consumer (K-30..K-32). Categories as in plan document 06: **A** = moves to Tabrion (the existing code
becomes a re-export or wrapper); **B** = stays and uses Tabrion inside; **C** = stays without Tabrion.

### 8.1 Axentra (required consumer `tabrion[postgres]`, K-30)

| Existing (place) | Cat. | Tabrion API | Differences / adjustment in Axentra |
|---|---|---|---|
| `Database._configure` (`database.py:273-275`) | B | `session_configurator(SessionSettings(statement_timeout_ms=t, lock_timeout_ms=None, idle_in_transaction_timeout_ms=None, application_name=None, timezone=None))` | identical including `commit()`; own pool only, never for `on_pool` (M10) |
| `Database.ping` (`database.py:348-357`) | B | `ping(self.pool, timeout=5).ok` | logging and the `bool` return stay |
| `Database.role_info`/`check_role` (`database.py:359-405`) | B | `role_info`/`check_role(owner_scope="rls")` | `RoleUnsafe` maps to `ConfigurationError` with today's texts |
| `roles.inspect_role`, `RoleReport` (`roles.py:30-104`) | A | `inspect_role`, `RoleReport` | re-export; `as_dict()` is a superset (plus `database`); API snapshot test in Axentra |
| `roles.grant_runtime_role` (`roles.py:107-190`) | A | `grant_runtime_role(conn, role, schema=..., protected_tables=("axentra_schema_migrations",))` | the URL variant stays a wrapper; catalog snapshot identical (TAB-T-R11) |
| `migrations.split_statements` (`migrations.py:125-171`) | A | `tabrion.sql.split_statements` | byte-identical (section 2.1) |
| `migrations._wait_for_lock` (`migrations.py:309-318`) | A | `wait_advisory_lock(conn, MIGRATION_LOCK)`, `advisory_unlock` | same polling semantics; the runner stays Axentra (C, G-EM = no) |
| Worker lock (own connection, `events/worker.py`) | B | `SessionLock` or `try_advisory_lock` | only if measurably less code (plan document 06) |
| 16 typed and 4 generic `except psycopg...` | B | `classify`, `safe_message` | table in section 1.3; B-06-1 |
| `tests/conftest.py` (throwaway database, roles without superuser, truncate) | A | `tabrion_postgres_database`, `tabrion_postgres_roles`, `truncate_tables` | same test count and skip list |
| `config/secrets.py:32-55` | C | - | own reference scheme, no gain |
| `DbScope`, `set_config(..., true)`, `joined`, `on_pool`, records, RLS, pool ownership | C | - | stays Axentra (K-30, R-SET) |

### 8.2 Nodestro (core and server profile, K-32)

| Existing (place) | Cat. | Tabrion API | Differences / adjustment in Nodestro |
|---|---|---|---|
| `SQLiteStore.__init__` (`storage.py:271-286`) | B | `connect(path, profile=SQLiteProfile(secure_delete=False))` | new: 0600 for new files, symlink rejection, `foreign_keys=ON` (neutral, F-01), `trusted_schema=OFF`; upgrade tests U1-U8 |
| `SQLiteStore._transaction` (`storage.py:313-332`) | B | `write_transaction` (outermost level) | reentrancy and RLock stay |
| `SQLiteStore.migrate` (`storage.py:563-637`) | B | `tabrion.sqlite.migrate(..., is_fresh=self._is_empty)` | `code` becomes `storage.<code>`; message without `{exc}` |
| `SQLiteStore.snapshot` (`storage.py:649-658`) | B | `tabrion.sqlite.backup(overwrite=True)` | 0600 (B-06-3), atomic, `quick_check` |
| nodestro-api `diagnostics.py:110-133` | B | `tabrion.sqlite.diagnose(run_quick_check=True)` | the FTS5 check stays |
| `PostgresDatabase._configure` (`storage_postgres.py:275-286`) | B | `session_configurator(SessionSettings(statement_timeout_ms=..., idle_in_transaction_timeout_ms=..., lock_timeout_ms=None, application_name=None, timezone=None, search_path=schema))` | identical GUCs; M10 unchanged |
| `try_/holds_/release_session_lock` (`:418-481`) | B | `SessionLock(conninfo, connect_kwargs=..., configure=...)` | the public methods stay wrappers |
| `role_info`, `inspect_role`, `grant_runtime_role` (`:679-689`, `:808-904`) | A | same names, `owner_scope="schema"`, `protected_tables=SCHEMA_VERSION_TABLES` | errors map to `storage.role_*`; dict wrappers |
| `pgvector_status` (`:907-968`) | B | `extension_status(conn, "vector", probe_type="vector")` | the message texts stay |
| `server_settings.py:115-141` (`*_FILE`) | B | `url_from_env` | identical rule |
| `tests/postgres_support.py` and 13 local fixtures | A | `tabrion.testing` (`configure(env_prefix="NODESTRO")`) | same test count and skip lists |
| `migration_checksum`, runner M9, `SharedPool`, job queue, ADR-0010 checker | C | - | stay (K-32) |

### 8.3 Orkexa (K-31)

| Existing (place) | Cat. | Tabrion API | Condition |
|---|---|---|---|
| `backends/postgres/connection.py` `PsycopgSource` (no timeouts, no ping) | B | `configure`, `ping`, `classify`/`safe_message` | `orkexa[postgres]` to `tabrion[postgres]` |
| `tests/backends/postgres/support.py` | A | `tabrion.testing` (`configure(env_prefix="ORKEXA")`) | dev extra; the DDL event trigger stays Orkexa |
| `_storage.py:80-166` | B | `tabrion.sqlite.connect`, `check_path`, `classify` | E-ORK-CORE = yes (MIG-ORK-3) |
| SQLite transactions x3 (`files.py:85-94`, `memory.py:84-92`, `index.py:473-500`) | B | `write_transaction`/`read_transaction` | E-ORK-CORE = yes (MIG-ORK-3) |
| Statement catalog, executor, partition guard, `sqlcheck`, schema mechanics | C | - | stay Orkexa |

**Golden files:** for every row of category A or B, the recorded behavior of the existing code lives in
`tests/golden/` of this repository: role catalog snapshots, splitter output, GUC values, error codes. Every file
names the SHA of the existing code (TAB-022a).

## 9. Example (layer B, use case A)

```python
from tabrion import Database, Profile
from tabrion.migrations import require_current

with Database.from_env("PARTS_DATABASE_URL", profile=Profile.default()) as db:
    require_current(db, script_location="migrations")  # SchemaNotCurrent if the Alembic state is missing
    with db.transaction() as tx:
        tx.execute("INSERT INTO parts (id, material) VALUES (:id, :material)", {"id": 1001, "material": "Aluminium"})
    parts = db.fetch_all("SELECT id, material FROM parts ORDER BY id")
```

The same example under SQLite (`sqlite:///parts.db`) and PostgreSQL is the acceptance test RA-A-3 (plan document 07).

## 10. Vector file format (internal test asset, K-35)

The vectors are **internal** Tabrion regression and behavior contracts. Other repositories do **not** vendor or load
them; integration runs through the package dependency (K-30..K-32). The field `vendored` and `vendor_check.py` are
dropped. The directory `conformance/` arrives with TAB-050.

### 10.1 JSON Schema (`conformance/schema/vector-file.schema.json`, draft 2020-12)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/peterkarlhuber/tabrion/conformance/schema/vector-file.schema.json",
  "type": "object",
  "required": ["format", "standard_version", "area", "vectors"],
  "additionalProperties": false,
  "properties": {
    "format": {"const": "ldp-vectors/1"},
    "standard_version": {"type": "string", "pattern": "^LDP-\\d+\\.\\d+\\.\\d+$"},
    "area": {"enum": ["sqlite-profile", "sqlite-transactions", "sqlite-migrate", "postgres-session",
                      "postgres-roles", "postgres-locks", "postgres-extensions", "errors-classification",
                      "errors-redaction", "sql-split"]},
    "vectors": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["id", "level", "rule", "engine", "action", "expect"],
        "properties": {
          "id": {"type": "string", "pattern": "^LDP-[A-Z]{2,4}-\\d{3}$"},
          "level": {"enum": ["MUST", "SHOULD", "INFO"]},
          "rule": {"type": "string", "description": "Reference to a section of SPEC.md, e.g. 'SQ-2.1'"},
          "engine": {"enum": ["sqlite", "postgres", "any"]},
          "min_server_version": {"type": "integer"},
          "since": {"type": "string"},
          "setup": {"type": "array", "items": {"type": "string"}, "description": "SQL statements or file state instructions"},
          "action": {
            "type": "object", "required": ["name"],
            "properties": {
              "name": {"enum": ["open_new_file", "open_existing", "inspect_pragmas", "concurrent_read_modify_write",
                                "write_in_read_transaction", "migrate", "session_settings", "role_report",
                                "grant_runtime_role", "lock_acquire", "lock_connection_lost", "extension_status",
                                "classify_error", "redact_error", "split_sql"]},
              "args": {"type": "object"}
            }
          },
          "expect": {"type": "object", "description": "action-specific; e.g. {'file_mode': '0600'}, {'kind': 'unique', 'retryable': false}, {'statements': [...]}"},
          "provenance": {"type": "string", "description": "Where the reference behavior lives, e.g. 'nodestro-runtime@816b3bc storage_postgres.py:808-864'"},
          "note": {"type": "string"}
        }
      }
    }
  }
}
```

### 10.2 Rules

- **Actions:** the action list is closed; a new action needs at least an LDP minor. `expect` per action is defined in
  a table in `SPEC.md` appendix A ("Action reference"), for example `classify_error`:
  `{"input": {"driver": "psycopg", "sqlstate": "23505"}, "expect": {"kind": "unique", "retryable": false,
  "outcome_unknown": false}}`.
- **No secrets:** vectors never contain real credentials. Redaction vectors use the marker `SECRET-4711` and expect
  it to be absent.
- **Checksum:** `conformance/MANIFEST.json` lists the SHA-256 per file. The CI job `conformance` checks it, so
  expectations change only on purpose and with a CHANGELOG entry.

## 11. Vector loader (internal, `tests/support/conformance.py`)

**Not public**, not in the wheel. Only Tabrion's own tests use it (K-35). Consumers test their integration with their
own tests against the Tabrion API.

```python
@dataclass(frozen=True, slots=True)
class Vector:   # id, level, rule, engine, action, args, expect, setup, min_server_version, provenance
    ...
def load(area: str) -> tuple[Vector, ...]
def verify_manifest() -> None
```

- It validates required fields, the ID pattern and unique IDs. The CI job `conformance` checks the full JSON Schema
  (dev dependency `jsonschema`).
- An error aborts the test and names the file and the ID, without content.
- Tests TAB-T-V01..V05: all files load, the manifest matches, a damaged file, a duplicate ID, Tabrion passes 100 % of
  the MUST and SHOULD vectors.

## Risks and decisions

| ID | Kind | Content | Proposal |
|---|---|---|---|
| R-T05 | Risk | The unified `grant_runtime_role` differs in edge cases (for example a schema with capital letters, partitioned tables) | Golden catalog snapshots of both existing functions (TAB-022a), TAB-031 |
| R-T06 | Risk | `SET LOCAL statement_timeout` for a single timeout stays active until the end of the transaction | Reset before every following statement (TAB-T-B14) |
| R-T07 | Risk | `cancel_safe` exists only from psycopg 3.2; the lower bound `>=3.2` covers it | Lowest-versions CI with psycopg 3.2.0 |
| E-T03 | Decision (product) | `fetch_one` raises on more than one row (instead of "first row") | Taken: raise (safer, catches missing WHERE clauses) |

## Open points for G-T1

The M1 stubs had to decide these points, which the plan leaves open or which conflict with typing rules. The G-T1
review (2026-11-08, "passed with conditions") decided each of them; G-9 and G-10 come from its conditions C-3 and
C-4. The deviation log is D-6..D-12 in [STATUS.md](../program/STATUS.md#deviation-log).

| # | Point | Decision |
|---|---|---|
| G-1 | Constructor of `TabrionError` (section 1.4 lists only attributes) | Accepted: `TabrionError(code, /, *, classification=None, original=None, statement=None, details=None)`; `DatabaseError.kind` is a property returning `classification.kind` (implementation notes for TAB-010: `args = (code,)` plus `__reduce__` for pickling; `DatabaseError.kind` must not fail without a classification, F-12) |
| G-2 | `SQLiteProfile.allow_shared_file` is mentioned only in prose (section 3.1) | Accepted: a field `allow_shared_file: bool = False` |
| G-3 | `tabrion.testing.profiles.ProfileCase` and the connection factory type are not specified (section 6) | **Revised (C-2).** The M1 proposal (`ConnectionFactory = Callable[[], sqlite3.Connection]`) was rejected: a profile case must choose the path, and portability cases must run on both engines. Now `SQLiteConnect = Callable[[Path], sqlite3.Connection]`, `ProfileCase(id, title, check: Callable[[SQLiteConnect, Path], None])` and `PortabilityCase(id, title, check: Callable[[Database], None])`; `portability_cases()` returns `list[PortabilityCase]` (section 6) |
| G-4 | `psycopg.Connection` is generic | Accepted: signatures use `psycopg.Connection[Any]` (mypy --strict requires the parameter); the meaning is "any row factory" |
| G-5 | Names in `__all__` beyond section 0 | Accepted: `tabrion.sqlite.Migration`, `tabrion.testing.profiles.SQLiteConnect` and `PortabilityCase` are public, because public signatures use them (`Statement`, `Params`: see the STATUS.md note on F-11) |
| G-6 | "`tabrion.check` (re-export)" in section 0 | Accepted: `tabrion.check` is the module and `tabrion.check.check` the function; `tabrion.check` is not re-exported as a function from the root, because a module and a function cannot share the attribute name |
| G-7 | `tabrion.postgres` and the extras | Accepted: psycopg is imported only for type checking in the M1 stubs; implementations import it inside the functions, so `import tabrion.postgres` works without psycopg (layer B imports it for `SessionSettings`) (TAB-015: a missing psycopg raises `ImportError` with the hint `pip install "tabrion[postgres]"`, tested under the blockade) |
| G-8 | `tabrion.postgres.roles` and `tabrion.postgres.locks` | Submodules with the role and lock names; `tabrion.postgres` re-exports them, so both import paths are public; `tabrion.postgres` is the canonical path in the docs and in `surface.json` |
| G-9 | `Database.health`: section 5.5 prose names `deep=True`, the signature has no `deep` (C-3) | `health(self, *, timeout: float = 5.0, deep: bool = False)`: keyword-only and additive, as doc 04 intends (`quick_check` only on request) |
| G-10 | `tabrion_postgres_database` has a "parameter `roles`", but a session fixture takes no arguments (C-4) | An overridable session fixture `tabrion_role_setup() -> RoleSetup \| None` (default `RoleSetup()`), which `tabrion_postgres_database` requests; one mechanism only, no `configure()` argument |

## Implementation notes (M2)

Points the implementation had to settle where the text above leaves room. None changes a signature or a name of
`surface.json`; each is recorded in the deviation log of [STATUS.md](../program/STATUS.md#deviation-log).

| # | Section | Note |
|---|---|---|
| I-1 | 1.3 | `classify` starts at the outermost object and follows `.orig` and `__cause__` breadth first, at most 5 links; `__context__` only as a fallback (I-13). Among the exceptions reached it takes the first driver error when it reports a lost connection, otherwise the first error with a SQLSTATE or a SQLite code, then the first driver error (a psycopg error without SQLSTATE, a `sqlite3.Error` without `sqlite_errorname`), then the outermost object. A driver error is recognized by its class's module (`psycopg`, `psycopg2`, `psycopg_pool`) or by `isinstance(..., sqlite3.Error)`; an object with a SQLSTATE attribute counts too. A `TabrionError` is classified by its own `classification`. "Never raises" covers `Exception`; a `SystemExit` or `KeyboardInterrupt` from a hostile `__str__` or property propagates |
| I-2 | 1.3 | `outcome_unknown = kind is CONNECTION and (sent or during_commit)`, with one exception: SQLSTATE 53300 is raised only while a connection is being established, so nothing ran; it is `retryable` without condition, as the mapping table says. Whenever `outcome_unknown` is true, `retryable` is false (R-NORETRY) |
| I-3 | 1.3 | A psycopg error without SQLSTATE: `OperationalError` (lost or refused connection, "the connection is closed", pool timeout) is CONNECTION; `DataError` is DATA; `PipelineAborted` and everything else is UNKNOWN. SQLAlchemy's `connection_invalidated` makes `connection_usable` false (and an UNKNOWN kind CONNECTION). Additional rows beyond the table, all additive: 57P04 and 57P05 CONNECTION; 25P04 (`transaction_timeout`, PostgreSQL 17) TIMEOUT without a usable connection; 23001 FOREIGN_KEY; classes 53 and 54 RESOURCE; SQLite `SQLITE_PERM` PERMISSION, `SQLITE_NOMEM` RESOURCE, `SQLITE_TOOBIG`, `SQLITE_MISMATCH` and `SQLITE_CONSTRAINT_DATATYPE` DATA. 08007 (`transaction_resolution_unknown`, CONNECTION) and 40003 (`statement_completion_unknown`, UNKNOWN) always set `outcome_unknown` and are never retryable, whatever `sent` and `during_commit` say. Any other SQLSTATE is UNKNOWN. SQLSTATE, SQLite code and object names are normalized to exact `str`, so a `str` subclass cannot steer the text |
| I-4 | 1.3 | The text of a driver error is read only to tell cases apart (57014 "statement timeout"; SQLite `SQLITE_ERROR` with "no such ..." or "syntax error"; SQLite errors without a code) and never reaches an output. SQLite object names come from "... constraint failed: t.a, t.b" and must be identifiers; a CHECK expression gives no name. Object names that are not printable or longer than 128 characters are dropped |
| I-5 | 1.3 | `safe_message` gives `"<kind text> (SQLSTATE x \| SQLite code, constraint c, table t, column c, outcome unknown)"`, each part only when present; a non-database exception gives `"database error"` |
| I-6 | 1.3 | `DbErrorRedactionFilter` follows Axentra's reviewed MIG-AXE-0 filter and goes further (review H-2, M-2): it finds database errors in `exc_info`, `args`, `msg` and inside lists, tuples, sets, dicts and exception `args` (3 levels, 1,000 objects); it walks the whole chain (not only 5 links) and exception groups; it checks the **formatted** message and replaces each found error's `str`, `repr`, string `args` and their escaped forms by the safe message; it removes `DETAIL:`/`HINT:`/`CONTEXT:`/`QUERY:`/`LINE n:` parts, plain or in escaped `repr` form (`\nDETAIL:`), with every continuation line up to the next traceback or SQLAlchemy boundary line, and `[parameters: ...]`; it handles mapping arguments; on an internal error it keeps the record with a fixed message and the redacted traceback. Since the TB-031 port (Axentra review `axe-tb031-review.md`): a removed part is replaced by the mark `[redacted: database server text]`, so truncation is visible (F-7); in free text it runs to the end, and only inside a formatted traceback (after a `Traceback (most recent call last):` header) does a whole structure line end it (F-4, re-review N-1); the inline label search is linear (F-3); a traceback is redacted also when only the database *text* is in an exception's message, `args` or notes, and errors held in an exception's `args` are found (F-1); a broken call (too few arguments, a missing key, a wrong type) is rewritten when a database error or its text is being handled or is in the template or an argument, so `logging`'s error report (which prints the chain and `Arguments: %r`) never runs for it (F-2, review T-1); a failing formatter or `emit` is not covered (SECURITY.md, TB-050); `db_sqlstate`/`db_constraint` name the first database error found in the record, also one in a suppressed context, unlike `classify`, which follows I-13 (review T-5); with a record-factory install the two names are reserved for `extra=` (review T-3); a mapping message stays a mapping with scrubbed values, and an `exc_info` inside it (structlog's event dict) is replaced by the redacted traceback under `exception` (F-5, D-1, D-2); attribute access on exceptions is guarded (D-3). After it rewrote `exc_text` it clears `exc_info`, so a formatter that formats `exc_info` itself (JSON formatters) cannot print the original; the exception object stays unchanged. Residuals are listed in SECURITY.md |
| I-7 | 1.4 | `TabrionError`: `args == (code,)`; `str` is `"<code> (k=v, ...): <safe message>"` (details and classification only when present), `repr` is `"Class('code')"`; `statement` is cut to 200 characters and never part of `str`/`repr`; `details` is a read-only copy; `__reduce__` pickles every attribute. `DatabaseError.kind` is `Kind.UNKNOWN` without a classification (F-12). The internal table `tabrion.errors._ERROR_CLASS` maps every `Kind` to its subclass for TAB-018 (RESOURCE to `ConnectionFailed`, as its docstring says) |
| I-8 | 1.4 | Tests TAB-T-E17..E20 (the plan names E01..E20 but defines only E01..E16): E17 `str`/`repr`/`details` without values, E18 pickling and copying, E19 `DatabaseError.kind`, E20 the kind-to-class table |
| I-9 | 2.1 | Differences from Axentra's splitter, all inputs Axentra splits wrongly by PostgreSQL's rules, besides the two named above: a dollar-quote tag may contain digits (`$a1$`) and non-ASCII letters (`$é$`) and a `$` inside an identifier (`a$b$`) starts no tag; a `--` comment also ends at a carriage return; a statement of nothing but block comments is dropped. An unterminated block comment counts as content, so the server reports it instead of the statement vanishing. Not handled, as in Axentra: parentheses (`CREATE RULE ... DO (...; ...)`) and `BEGIN ATOMIC` bodies. Evidence: `tests/golden/axentra_splitter.json` (every SQL file of Axentra 9c4666f, byte-identical) |
| I-10 | 2.3 | `safe_url`: in URL form the user information ends at the last `@` before the first `/`, as libpq parses it (review M-1: a raw `?`, `&` or `=` in a password), or at the last `@` before the query when that is later (a raw `/` or `#` in a password); more is hidden rather than less. A password with both a raw `/` and a raw `?` makes the input unparseable rather than half masked. Keyword conninfo follows libpq's quoting and backslash rules; `password` and `sslpassword` are masked, in either case, as keywords and as query parameters. An empty string stays empty |
| I-11 | 2.3 | `url_from_env`: an empty variable and an empty file count as not set. Three codes beyond the contract: `config.unreadable` (the file is missing, unreadable or not UTF-8), `config.not_regular_file` (a directory, FIFO or device; opened without blocking and checked with `fstat` before reading) and `config.too_large` (more than 64 KiB). Every error is raised outside an `except` block, so it carries no `__context__` with the path or the bytes; the file descriptor is always closed. The details name the variable (`{"name": ...}`), never its value or the path |
| I-12 | `pyproject.toml` | `tabrion.config` raises `ConfigurationError`, so it imports `tabrion.errors`: the layers contract now has `tabrion.errors` as its own bottom layer under `tabrion.sql \| tabrion.config` (they stay independent of each other); three new mutation probes |
| I-13 | 1.3 | **Amends the "Chains" bullet** (review H-1, deviation D-26). `__context__` records what was being *handled* when an error was raised, not what caused it: a connection lost during COMMIT inside an `except` block for a 40001 carries that 40001 as its context. Taking "the first error with a SQLSTATE" along `__context__` therefore reported a lost COMMIT as a retryable serialization failure, which breaks R-NORETRY. So `__context__` is followed only while the exceptions reached through `.orig` and `__cause__` hold no driver error, never when `__suppress_context__` is set, and an outer connection loss (class 08, 57P01..57P05, psycopg `OperationalError` without SQLSTATE) wins over any SQLSTATE error deeper in the chain |
| I-14 | 4.2 | **`ping`** runs in `connection.transaction(force_rollback=True)`: `BEGIN ... ROLLBACK` on an IDLE connection (with or without autocommit), a savepoint that is rolled back inside a running transaction (deviation D-28). So the `SET LOCAL statement_timeout` ends with it in every state, and a timeout leaves the caller's transaction usable; in an aborted transaction (INERROR) ping opens no savepoint (psycopg would log its failed rollback, review I-1): its `SELECT` fails with 25P02, ping gives `ok=False` and leaves the transaction aborted. As the first statement of a caller's fresh transaction, ping (like every reader, I-16) takes that transaction's snapshot, which a rollback to a savepoint does not undo: a later `SET TRANSACTION ISOLATION LEVEL` fails, and a REPEATABLE READ or SERIALIZABLE transaction sees the data as of the check (review L-1). It sends `SET LOCAL statement_timeout = <ms>` (milliseconds rounded up, at least 1), not `set_config`: `SET` takes no snapshot, so the limit covers the `SELECT` from its start, including a wait for a safe snapshot or a lock. `latency_ms` is the round trip of that `SELECT`. The limit is the server's: a network that stops answering is bounded by the connection's TCP settings, not by `timeout` (SECURITY.md). A pool lends with `timeout`; anything it raises except `TypeError` is a failed checkout, classified with `sent=False`; a pool that lends something other than a `psycopg.Connection` raises `TypeError`. A `timeout` that is not a number raises `TypeError`, one that is not positive and finite `ValueError` (D-29) |
| I-15 | 4.1 | **`configure` validation.** Timeouts are `int` (not `bool`) from 0 to 2 147 483 647; `application_name` is printable ASCII (0x20..0x7E), at most 63 characters; `search_path` follows the regex and is sent quoted (`"name"`, as Nodestro does), so it is case-sensitive; `timezone` is a non-empty `str` without NUL, and the server decides whether it knows the name (its error passes through). Every violation raises `ConfigurationError("postgres.invalid_setting")` with `details={"setting": <field>}`, never the value. R-SET is enforced over the fields of the object: a subclass field that is not `None` raises `ConfigurationError("postgres.setting_not_allowed")` before anything is sent (TAB-T-G03); a `settings` that is no `SessionSettings` raises `TypeError`. With nothing to set, `configure` sends nothing and opens no transaction. In the caller's running transaction (INTRANS) the `SELECT` runs under a savepoint (two more round trips): a value the server rejects (an unknown time zone) raises its error and leaves the caller's transaction usable, with nothing set (review M-1). `transaction_timeout` is dropped below `server_version` 170000 without a round trip |
| I-16 | 4 | **All functions.** Each reads through `psycopg.Cursor(connection, row_factory=tuple_row)`, whatever the connection's row factory and `cursor_factory` (`ClientCursor`, `RawCursor`). Every catalog function, relation, type and operator is qualified with `pg_catalog` (`::pg_catalog.text`, `OPERATOR(pg_catalog.=)`; `::int` and `::bool` map to `pg_catalog` in the grammar), so a schema that the caller's `search_path` puts before `pg_catalog` cannot replace them or run its code as the caller (review M-2; TAB-T-G01 traps functions, operators and domains). A connection that is not a synchronous `psycopg.Connection` raises `TypeError`. Argument values outside their domain (`owner_scope`, an extension name) raise `ValueError`, before anything is sent. In the caller's running transaction (INTRANS, also inside `connection.transaction()`) a reader's statements run under a savepoint: one that fails raises its error and leaves the caller's transaction usable, with its writes, instead of aborting it (review M-1; two more round trips). In an aborted transaction the readers raise the driver error (25P02) and leave the transaction as it is. The implicit transaction a reader opened is rolled back also after an error. A reader that runs first in a caller's fresh transaction takes its snapshot (see I-14) |
| I-17 | 4.3 | **`diagnose`.** The three timeouts come from `pg_settings` (unit ms) when the role may read it, otherwise from `current_setting` (`0`, `500ms`, `30s`, `5min`, `1h`, `1d`). `archive_mode` and `wal_level` are `None` when `pg_settings` is not readable or hides the row; `ssl` is `None` without `SELECT` on `pg_stat_ssl`. `user` is `current_user`; `role` is `role_info` with `owner_scope="schema"` in the same transaction. `host_is_local` holds for an empty host, a Unix socket directory or abstract name (`/...`, `@...`), `localhost` (any case) and loopback addresses (an IPv6 zone is ignored) |
| I-18 | 4.4 | **`extension_status`.** `active` needs the extension's schema in `current_schemas(true)` also without `probe_type`; installed but not usable (another schema, or a `probe_type` that does not resolve) is `other_schema`, as in Nodestro. `available_version` is `default_version` of `pg_available_extensions`. The commands quote the extension name too (`CREATE EXTENSION "vector" SCHEMA "public";`), where Nodestro writes it bare (D-30); they name `"public"` when the connection has no current schema (Nodestro). A name that does not match the regex raises `ValueError`; so does a `probe_type` the server cannot parse (`to_regtype` raises 42601 for it; the message does not repeat the value), on servers whose `to_regtype` returns `NULL` instead the probe just does not resolve |
| I-19 | 4.5, 4.6 | **Roles.** `role_info` reports `current_user` (the effective role after `SET ROLE`). `check_role` lists the reasons in the order `superuser`, `bypass_rls`, `table_owner`, comma separated; a table owner is allowed by `allow_table_owner` or `allow_superuser`. `RoleMissing("postgres.role_missing")` carries no details (the caller has the name). `inspect_role` names the schema `""` when neither `schema` nor `current_schema()` gives one, and then reports no tables; a `schema` that does not exist is named as given, with no tables and `can_create=False` (it is looked up by its exact name; review M-1); `owned_tables` is ordered by `relname` (server collation) as in both existing functions; `as_dict` returns the keys in the order of section 4.6 and a new list for `owned_tables` |
| I-20 | 4.5, 4.6 | **Roles a session can become** (review H-1, decided by the owner within the G-T1 freeze: signatures and the set of reason codes unchanged, the check stricter). `rolsuper` and `rolbypassrls` are not inherited, but a member of a role can `SET ROLE` to it (a NOINHERIT member too), and a session can `RESET ROLE` to its `session_user`. So `role_info` (and with it `check_role` and `diagnose.role`) reports `superuser` and `bypass_rls` when `current_user` **or `session_user`** is a member (`pg_has_role(..., 'MEMBER')`, direct or indirect) of a role with that attribute, and `table_owner` when either is a member of the owner of a table in scope; `role` stays `current_user`. `inspect_role` reports `superuser` and `bypass_rls` when the named role is a member of such a role. A superuser session after `SET ROLE app` is therefore reported as superuser. On PostgreSQL 16+ a membership granted `WITH SET FALSE, INHERIT FALSE` cannot be used, but still counts: the check errs on the strict side. A superuser is a member of every role, so its `bypass_rls` is true whenever any role has BYPASSRLS, also when the superuser itself was created NOBYPASSRLS (the reference queries then say `false`; TAB-T-R02/R10 assert this, review DL-2 of #4). The reference queries of Axentra and Nodestro read the attributes of `current_user` only (D-32); predefined roles such as `pg_execute_server_program` are not reported (TODO TB-036) |
| I-32 | 4.7 | **Locks** (TAB-030). `lock_key` hashes the UTF-8 bytes of `name`; a non-`str` raises `TypeError`, a `str` without a UTF-8 form (an unpaired surrogate) `ValueError`. Keys are checked before anything is sent, by their exact `int` value (an `int` subclass cannot lie about it): a `bool` or non-`int` raises `TypeError`, a value outside int64 `ValueError`; so do a `poll_interval` that is not positive and finite and a `timeout` that is negative or NaN. `SessionLock` refuses `connect_kwargs={"autocommit": False}` (`ValueError`) and never shows the conninfo in `repr`. A database error of an attempt passes through and is never repeated (R-NORETRY); when it lost the connection, every count is cleared. A key that was held on a lost connection is not taken again silently: `try_acquire` and `acquire` raise `UsageError("postgres.lock_lost")` for it until `holds(key)` (which returns `False`) or `release(key)` has acknowledged the loss, so a nested section cannot take a fresh hold whose `release` frees the lock an outer section relies on (review L-3 of #6, D-43); other keys are not affected. `held_keys()` does not acknowledge a loss: it returns the keys still held while `try_acquire` raises for the lost one, and `acquire(timeout=...)` raises `lock_lost` at once instead of polling until the timeout (delta re-review of #6). Threads of one process share the holds (advisory locks are re-entrant per session). `release` of a key that is not held does nothing; if the connection fails while unlocking, it is dropped, which ends the session and its locks. `close()` is idempotent. Fork: the child detaches the inherited connection without a round trip (its socket is pointed at `/dev/null` and the object kept alive, so neither the child's use nor its garbage collection can end the parent's session) and opens its own; a fork hook (`os.register_at_fork`) gives every live `SessionLock` a new internal lock in the child, so a fork while another thread is inside a method cannot hang the child (review L-2 of #6). A `configure` callback must not fork: a child that returns into the method would use the parent's socket mid-operation and end the parent's session (review DI-1 of #6). A half-open TCP connection keeps the session and its locks on the server until TCP gives up: pass keepalives or `tcp_user_timeout` in `connect_kwargs` (SECURITY.md). `try_advisory_lock` and `advisory_unlock` follow I-16 (a savepoint in a running transaction; the session-level lock outlives it). `wait_advisory_lock` refuses INTRANS, INERROR and ACTIVE (after syncing a caller's pipeline, I-34), and in pipeline mode syncs each attempt before it sleeps, so no statement or snapshot stays open while it waits (review L-1 of #6); a closed connection's error passes through from the driver. Without autocommit each attempt is a `SELECT` and the rollback of its implicit transaction: a sample of `pg_stat_activity` may meet that moment as `idle in transaction` (L12 checks that idle samples hold no snapshot and that L13 completes) |
| I-33 | 6 | **Fault proxy** (TAB-034, QA-3; TODO TB-033, decided). `tabrion.testing._faults` is **internal and unstable**: underscore-private, not in `surface.json`, not exported from `tabrion.testing`, free to change in any release; Tabrion's own tests use it (TAB-T-170, TAB-T-E14). `FaultProxy` follows the protocol framing and fires one armed fault on the next `Query` or `Parse` that contains the given bytes: `reply` (the statement runs, the replies up to `ReadyForQuery` are dropped, then both sides are closed), `inflight` (the batch reaches the server, then both sides are closed), `request` (never forwarded), `delay` (the answer is held back). A public `tabrion.testing.faults` is a 0.2 surface decision by contract addendum and review |
| I-34 | 4 | **Pipeline mode** (review DL-1). Inside `with connection.pipeline():` the transaction status is ACTIVE until a sync. Every function first syncs the caller's pipeline (`Pipeline.sync()`), so the savepoint of I-15/I-16 applies there too and a failing statement does not abort the caller's pipeline transaction. The sync sends what the caller had queued; an error of a queued statement is the caller's and propagates from the call, from `ping` too (outside its "never raises": reported as a failed ping, psycopg would not raise it again and the caller's `COMMIT` would end the aborted transaction silently; review M-1 of #6) |
| I-35 | 4.6 | **`grant_runtime_role`** (TAB-031). The arguments are checked first (`TypeError`: `role` a non-empty `str`, `schema` a non-empty `str` or `None`, `protected_tables` a sequence of non-empty `str` and not a `str`, `grant_connect` a `bool`). It needs a transaction of its own: after syncing a caller's pipeline (I-34) any status but IDLE raises `UsageError("postgres.nested_transaction")` before anything is sent (T-NOTX; `connection.transaction()` would otherwise be a savepoint in the caller's transaction, committed only with it). `ValueError` when neither `schema` nor `current_schema()` names a schema, and for a `role`, `schema` or protected table name longer than 63 bytes in UTF-8 (`NAMEDATALEN - 1`: PostgreSQL would truncate it and grant on the object whose name is the prefix; review L-2 of #8, D-50); `UsageError("postgres.schema_missing")` for a schema that does not exist (review L-4, D-50); no message names the object. Refused before anything is granted (`RoleUnsafe`, reasons from the frozen set): `superuser` and `bypass_rls` as `inspect_role` reports them (also through membership, I-20); `table_owner` when `role` is the connecting role, can become it (`pg_has_role(role, CURRENT_USER, 'MEMBER')`, NOINHERIT members too: such a role owns every table the owner creates, D-46), is or can become the owner of the schema (`pg_has_role(role, nspowner, 'MEMBER')`: it may drop or alter every object in it; review M-1 of #8, D-49) or owns tables of the schema. Check the result as well: `connect=False` when the owner may not grant `CONNECT` (PostgreSQL only warns), and `can_create=True` (`CREATE` on the schema that someone else granted, which the function keeps) means the role is **not** restricted although `restricted` (frozen: superuser, BYPASSRLS, owned tables) holds: it can plant functions, operators or tables in the schema (review L-1, I-3). The seven statements of both references run in their order in one `connection.transaction()` through Tabrion's own `tuple_row` cursor, every name quoted with `psycopg.sql.Identifier`. Each of `protected_tables` is looked up by its exact name in the target schema only (`pg_class` joined with `pg_namespace`, qualified operators; no `search_path`, no identifier parsing, D-47) and, when it exists, gets `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema.table`; a missing one is skipped. `GRANT CONNECT ON DATABASE current_database()` follows in a second transaction: PostgreSQL turns a grant by a role without the grant option into a warning (nothing granted) and raises `InsufficientPrivilege` only for a grantor with no privilege at all on the database; both leave `connect=False`. A connection lost in the first transaction leaves nothing granted; a loss between the two keeps the grants (the function is idempotent: run it again). As in both references, `GRANT EXECUTE ON ALL FUNCTIONS` does not cover procedures (PUBLIC keeps `EXECUTE` on them by default), and a run without `protected_tables` grants the bookkeeping tables write access again. Tests TAB-T-R11..R18; the H-1 tests of `role_info` that were named R11/R12 are R19/R20 (D-48) |
