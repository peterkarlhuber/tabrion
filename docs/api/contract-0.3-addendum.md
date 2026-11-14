# API contract addendum 0.3 (CR-DBX, planned)

!!! warning "Planned 0.3, not implemented"
    Everything on this page is **planned for Tabrion 0.3 and not implemented**. None of these names exists in the
    code, in `__all__` or in [`surface.json`](surface.json); none may be imported or relied on. Names, signatures and
    error codes may still change until the independent API review of TAB-XW-10. When a work package implements its
    part, it moves that section into [the contract](contract.md) with implementation notes, adds the names to
    `surface.json` with `"since": "0.3"` and stability `X`, and deletes the section here.

Work package TABRION TAB-XW-1 (change request CR-DBX, planning document 15 sections 5 and 9.1). The design rules are
[ADR 0015](../adr/0015-crdbx-write-and-ddl-primitives.md); the scope is [ADR 0014](../adr/0014-crdbx-tabrion-scope.md).
The conditions K-1..K-3, decisions D-1..D-4 and notes N-1..N-5 of the security review of #76 are binding
requirements; each is repeated below at its function with its named test, and the full table is
[ADR 0015 section 9](../adr/0015-crdbx-write-and-ddl-primitives.md#9-conditions-decisions-and-notes-of-the-security-review-of-76-binding-requirements).
The conventions of the contract apply unchanged: keyword-only parameters after `*`, frozen slotted dataclasses, times
in seconds as `float`, T-NOTX for connections handed to Tabrion, R-SET, R-NORETRY, no values in errors.

## A.0 Overview

Every planned name is experimental (`X`) and arrives with 0.3. No stable name changes.

| Module | Planned names | WP | Extra |
|---|---|---|---|
| `tabrion.schema` | `ForeignKeyShape`, `UniqueShape`, `CheckShape`, `IndexShape`; new fields of `ColumnShape`, `TableShape`, `SchemaSnapshot`; `snapshot(level=)`; new `SchemaDifference.kind` values | TAB-XW-2 | `sqlalchemy` |
| `tabrion.ddl` (new) | `DdlStatement`, `create_statements`, `table_statements` | TAB-XW-3 | `sqlalchemy` |
| `tabrion.plan` (new) | `RiskClass`, `PlanStep`, `MigrationPlan`, `plan`, `revision_text`, `migration_markers`, `ExecutionResult`, `execute_plan` | TAB-XW-4, TAB-XW-5 | `sqlalchemy`; `revision_text` also `migrations` |
| `tabrion.rowedit` (new) | `insert_row`, `update_row`, `delete_row` | TAB-XW-6 | `sqlalchemy` |
| `tabrion` (layer B) | `Transaction.execute_checked` | TAB-XW-6 | `sqlalchemy` |
| `tabrion.sqlite` | `restore`, `RestoreResult`, `delete_database_file` | TAB-XW-7 | none (standard library) |
| `tabrion.postgres` | `RoleOptions`, `RolePrivileges`, `role_privileges`, `create_role`, `alter_role`, `drop_role`, `grant_membership`, `revoke_membership`, `grant_privileges`, `revoke_privileges` | TAB-XW-8 | `postgres` |
| `tabrion.postgres` | `create_database`, `drop_database`, `database_sessions` | TAB-XW-9 | `postgres` |
| `tabrion.profilefile` | `ConnectionProfile.mode` value `"admin"` | TAB-XW-8 (E-X8) | none |
| `tabrion.errors` | `RowCountMismatch`, `PlanError`, `PlanMismatch`, `SchemaDrift`, `PlanRefused`, `PlanFailed`, `DatabaseInUse`; new `RoleUnsafe` reasons | TAB-XW-4..9 | none |

**Not planned** (non-goals stay, ADR 0014, ADR 0015): a `pg_dump`/`pg_restore` wrapper, an Alembic runner or
`upgrade`, a migration version table, a feature flag, gate or "writes enabled" setting, upsert or merge helpers, role
attributes SUPERUSER, BYPASSRLS, CREATEROLE and REPLICATION, libpq multi-host profiles (W-10, TB-148 (1)).

## A.0.1 Planned errors (`tabrion.errors`, since 0.3, X)

```python
class RowCountMismatch(TabrionError)   # "transaction.rowcount_mismatch" (details expected, actual)
                                        # "transaction.too_many_rows" (details limit, actual)
class PlanError(TabrionError)          # base of the plan errors
    class PlanMismatch(PlanError)          # "plan.hash_mismatch", "plan.fingerprint_mismatch", "plan.invalid"
    class SchemaDrift(PlanError)           # "plan.drift": details expected, actual (fingerprints), differences (count)
                                           # .differences: tuple[SchemaDifference, ...]
    class PlanRefused(PlanError)           # "plan.refused": details reason in
                                           # {"environment", "migration_managed", "risk_not_allowed",
                                           #  "dialect_mismatch", "engine_unsupported", "unsafe_schema_sql"},
                                           # markers (comma list)
    class PlanFailed(PlanError)            # "plan.failed": details step, completed, state in {"rolled_back", "partial"};
                                           # original set; never chained
class DatabaseInUse(TabrionError)      # "postgres.database_in_use" (details sessions), "sqlite.in_use"
```

- `RoleUnsafe` (exists, `postgres.role_unsafe`) gets new values in `details["reasons"]`: `createrole`,
  `replication`, `reachable_superuser`, `reachable_bypass_rls`, `reachable_createrole`, `reachable_replication`,
  `predefined_role`, `owns_objects`, `current_user`, `security_definer`, `schema_create`. New `UsageError` codes:
  `postgres.role_ddl_requires_16` (D-1), `plan.invalid_revision` (K-3). New reason values and new error classes are additive (API
  stability policy S-4).
- The rules of contract section 1.4 apply: `str` is the safe message, no `__cause__`, `details` never hold data
  values, passwords, revision text or SQL with literals. A fingerprint and a plan hash are not secrets.

## A.1 `tabrion.schema` v2 (TAB-XW-2)

```python
@dataclass(frozen=True, slots=True)
class ColumnShape:
    name: str
    type: str
    nullable: bool
    default: str | None = None        # new: the server default as the dialect reflects it; level 2 only


@dataclass(frozen=True, slots=True)
class ForeignKeyShape:
    name: str | None
    columns: tuple[str, ...]
    referred_table: str               # "schema.table"
    referred_columns: tuple[str, ...]
    on_delete: str | None = None      # "CASCADE", "SET NULL", "RESTRICT", "NO ACTION", "SET DEFAULT"
    on_update: str | None = None


@dataclass(frozen=True, slots=True)
class UniqueShape:
    name: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckShape:
    name: str | None
    sqltext: str                      # as the dialect reflects it


@dataclass(frozen=True, slots=True)
class IndexShape:
    name: str
    columns: tuple[str, ...]          # an expression index gives its expression text
    unique: bool = False


@dataclass(frozen=True, slots=True)
class TableShape:
    name: str
    columns: tuple[ColumnShape, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyShape, ...] = ()       # new, level 2
    unique_constraints: tuple[UniqueShape, ...] = ()     # new, level 2
    check_constraints: tuple[CheckShape, ...] = ()       # new, level 2
    indexes: tuple[IndexShape, ...] = ()                 # new, level 2


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    dialect: str
    tables: tuple[TableShape, ...]
    level: Literal[1, 2] = 1                             # new


def snapshot(inspector: sqlalchemy.Inspector, *, tables: Iterable[str] | None = None,
             schemas: Iterable[str] | None = None, level: Literal[1, 2] = 1) -> SchemaSnapshot: ...
def fingerprint(snapshot: SchemaSnapshot) -> str: ...  # "v1:sha256:<hex>" for level 1, "v2:sha256:<hex>" for level 2
```

- **Levels.** Level 1 is the 0.2 shape (columns, type, nullability, primary key) and its fingerprint stays
  byte-identical, so a stored `v1:` fingerprint (Nodestro NOD-CR-5, profile `schema_fingerprint`) stays valid.
  Level 2 adds defaults, foreign keys, unique and check constraints and indexes. `from_json` reads both;
  `ConnectionProfile.schema_fingerprint` accepts `v1:` and `v2:`.
- **New `SchemaDifference.kind` values (level 2):** `column_default`, `foreign_key_missing`, `foreign_key_added`,
  `unique_missing`, `unique_added`, `check_missing`, `check_added`, `index_missing`, `index_added`. A new difference
  names the table, the column where there is one, and the constraint or index name in a new field
  `SchemaDifference.name: str | None = None`; never data. Only the `_added` kinds of indexes are not breaking.
- **D-4:** level 2 stays opt-in in 0.3; `level=1` stays the default. Tests `tests/unit/test_schema_v2.py::test_default_level_is_1`,
  `::test_v1_fingerprint_unchanged_against_02_golden`.
- `compare` refuses snapshots of different levels (`UsageError("schema.level_mismatch")`).
- A level-2 `SchemaSnapshot` is also the **target model** that the Schema Designer edits and that `tabrion.ddl` and
  `tabrion.plan` take; a consumer builds it with `dataclasses.replace` or `from_json`. There is no separate model.
- **Tests:** shapes on both engines, a stable v1 fingerprint over the 0.2 golden snapshots, each new difference
  kind, round trip through JSON, SQLite unnamed constraints.

## A.2 `tabrion.ddl` (TAB-XW-3)

```python
@dataclass(frozen=True, slots=True)
class DdlStatement:
    sql: str              # dialect SQL compiled by SQLAlchemy; identifiers quoted, no bound values
    target: str           # "schema.table", "schema.table.column" or the index or constraint name
    transactional: bool   # False for CREATE INDEX CONCURRENTLY and other non-transactional statements


def create_statements(model: SchemaSnapshot, *, dialect: Literal["sqlite", "postgresql"]
                      ) -> tuple[DdlStatement, ...]: ...           # every table in dependency order, then indexes
def table_statements(table: TableShape, *, dialect: Literal["sqlite", "postgresql"]
                     ) -> tuple[DdlStatement, ...]: ...            # CREATE TABLE and its indexes
```

- Compiled with SQLAlchemy Core (`CreateTable`, `CreateIndex`) against the named dialect, without a connection. A
  type text that the dialect cannot parse back raises `UsageError("ddl.unsupported_type")` naming the column; an
  identifier longer than the PostgreSQL limit of 63 bytes raises `UsageError("ddl.identifier_too_long")` (no silent
  truncation, review L-2 of #8). A `level=1` model raises `UsageError("ddl.level_required")`.
- A default or check text is taken from the model as SQL text; it is schema, not data. The consumer must show it
  in the preview before it reaches `tabrion.plan`.
- **Tests:** golden DDL for both dialects, a round trip (create, snapshot, `compare` empty), quoting of reserved
  words and mixed case.

## A.3 `tabrion.plan`: diff, plan and revision text (TAB-XW-4)

```python
class RiskClass(StrEnum):
    ADDITIVE = "additive"
    DATA_CHANGING = "data_changing"           # type change, NOT NULL on existing rows, default backfill
    DESTRUCTIVE = "destructive"               # drop table, column, constraint, narrowing type
    REBUILD = "rebuild"                       # SQLite table rebuild
    LOCKING = "locking"                       # PostgreSQL ACCESS EXCLUSIVE on an existing table
    NON_TRANSACTIONAL = "non_transactional"


@dataclass(frozen=True, slots=True)
class PlanStep:
    index: int
    operation: str                            # "create_table", "drop_table", "add_column", "drop_column",
                                              # "alter_column", "add_constraint", "drop_constraint",
                                              # "create_index", "drop_index", "rebuild_table"
    target: str
    statements: tuple[DdlStatement, ...]
    risks: tuple[RiskClass, ...]              # sorted, at least one
    transactional: bool


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    dialect: str
    source_fingerprint: str                   # "v2:sha256:..." of the snapshot the plan starts from
    target_fingerprint: str
    steps: tuple[PlanStep, ...]
    plan_hash: str                            # "sha256:<64 hex>" over the canonical JSON of all fields above

    @property
    def risks(self) -> tuple[RiskClass, ...]: ...
    def to_json(self) -> str: ...
    @classmethod
    def from_json(cls, text: str) -> MigrationPlan: ...   # recomputes plan_hash; PlanMismatch("plan.invalid")


def plan(current: SchemaSnapshot, target: SchemaSnapshot, *,
         server_version: tuple[int, ...] | None = None) -> MigrationPlan: ...
def revision_text(plan: MigrationPlan, *, revision: str, down_revision: str | None,
                  message: str) -> str: ...  # extra migrations
def migration_markers(database: Database) -> tuple[str, ...]: ...
```

- `plan` needs two level-2 snapshots of the same dialect (`PlanRefused`, reason `dialect_mismatch`;
  `UsageError("plan.level_required")`). It orders steps so that dependencies hold (drop foreign keys before tables,
  create referenced tables first). On SQLite an `ALTER` that SQLite cannot do in place becomes one `rebuild_table`
  step (new table, copy, drop, rename, indexes, triggers and views recreated from `sqlite_schema`); `server_version`
  decides whether `DROP COLUMN` (3.35 and later) is possible in place.
- **K-1 (schema SQL from the file is attacker data):** every text taken from `sqlite_schema` for a rebuild becomes
  exactly one `DdlStatement` and is run with `execute`, never `executescript`. It must pass
  `sqlite3.complete_statement` with no remainder after the first statement, and its head must match the row's
  `type` and name (`CREATE [TEMP] TRIGGER <name>`, `CREATE [TEMP] VIEW <name>`, `CREATE [UNIQUE] INDEX <name>`).
  Otherwise `plan` refuses (`PlanRefused`, reason `unsafe_schema_sql`), and `execute_plan` checks again. Tests
  `tests/unit/test_plan_sqlite_rebuild.py::test_rebuild_refuses_second_statement_in_schema_sql`,
  `::test_rebuild_refuses_type_or_name_mismatch`, `::test_rebuild_never_uses_executescript`.
- The plan is deterministic: the same two snapshots and server version give the same `plan_hash`.
- **`revision_text`** renders an Alembic revision file (`upgrade()` with `op.*` calls, `downgrade()` raising
  `NotImplementedError` unless every step is reversible) as a string. It imports Alembic's operation renderers and
  nothing else: no `env.py`, no project module, no file access. `revision`, `down_revision`, `message` and every
  identifier are written as Python string literals.
- **K-3:** the module docstring is a fixed text with no caller text; the message appears only as a `repr` literal,
  so `"""`, `\`, a line break or NUL cannot break out. `revision` must match `[0-9a-zA-Z_]{1,64}` and
  `down_revision` the same or `None` (`UsageError("plan.invalid_revision")`). Tests
  `tests/unit/test_plan_revision_text.py::test_message_cannot_break_docstring` (`"""`, `\`, `\r\n`, `\x00`),
  `::test_revision_ids_validated` (a `down_revision` of `"; import os`), `::test_revision_text_ast_has_only_literals`
  (each output through `ast.parse`). The file name or slug is Studio's (STU-XSD-3: no path parts, no `..`).
- **N-2:** `from_json` proves integrity, not authenticity: a consumer never takes a plan from an untrusted source
  (project file, URL, import); Studio keeps plans server-side (Studio #109 TX-08). Test
  `tests/unit/test_plan.py::test_from_json_docstring_states_integrity_only`. SQLite rebuilds render as `op.batch_alter_table(..., recreate="always")`
  with a comment that points to the migrations guide (foreign keys off around a batch rebuild).
- **`migration_markers`** reads the catalog only and returns the markers of migration management it finds:
  `"alembic_version"`, `"nodestro_schema_migrations"`, `"axentra_schema_migrations"`, `"sqlite_user_version"`. An
  empty tuple means none was found.
- **Tests:** every risk class, plan determinism, `from_json` refusing an edited plan, revision text golden files and
  an injection case (a table named `x"); import os; ("`), and an import and file-access probe that `revision_text`
  touches no project path.

## A.4 `tabrion.plan.execute_plan` (TAB-XW-5)

```python
@dataclass(frozen=True, slots=True)
class ExecutionResult:
    plan_hash: str
    dry_run: bool
    steps_run: int
    untested_steps: tuple[int, ...]   # dry run: the non-transactional steps that did not run
    fingerprint_after: str | None     # None for a dry run


def execute_plan(
    database: Database,
    plan: MigrationPlan,
    *,
    plan_hash: str,
    expected_fingerprint: str,
    environment: Literal["dev", "test"],
    allow: Iterable[RiskClass] = (RiskClass.ADDITIVE,),
    dry_run: bool = False,
    lock_timeout: float = 5.0,
    timeout: float | None = None,
) -> ExecutionResult: ...
```

Order of checks, each before any statement runs:

1. `environment` is `"dev"` or `"test"`, else `PlanRefused` (reason `environment`).
2. `migration_markers(database)` is empty, else `PlanRefused` (reason `migration_managed`, details `markers`).
3. The plan's recomputed hash equals `plan.plan_hash` and `plan_hash`, else `PlanMismatch("plan.hash_mismatch")`;
   `plan.source_fingerprint == expected_fingerprint`, else `PlanMismatch("plan.fingerprint_mismatch")`.
4. Every risk of every step is in `allow`, else `PlanRefused` (reason `risk_not_allowed`).
5. The database's dialect equals `plan.dialect`, else `PlanRefused` (reason `dialect_mismatch`).
6. **Drift check:** a level-2 snapshot taken inside the execution transaction (PostgreSQL) or under
   `BEGIN IMMEDIATE` (SQLite) has the fingerprint `expected_fingerprint`, else `SchemaDrift`. **N-1:** on PostgreSQL
   the executor first locks the plan's target tables (`LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE` or the stronger
   mode a step needs, under `lock_timeout`) and only then computes the fingerprint. Tests
   `tests/postgres/test_plan_execute_postgres.py::test_locks_taken_before_drift_fingerprint`,
   `::test_concurrent_ddl_after_check_blocked`.

- **D-3:** `environment` has no default and is typed `Literal["dev", "test"]`; it is a guard rail, not a boundary.
  Studio fills it from the user-local stored profile, never from the request (Studio #109 K-1). Tests
  `tests/unit/test_plan_execute.py::test_environment_is_required_keyword_without_default`,
  `::test_environment_other_values_refused`.
- **K-1 at execution:** each recreated `sqlite_schema` text runs as one statement on the caller's connection under
  the caller's authorizer, with `trusted_schema = OFF` and defensive mode (`SQLITE_DBCONFIG_DEFENSIVE`; Studio
  TX-16). Test `tests/unit/test_plan_sqlite_rebuild.py::test_rebuild_runs_under_authorizer_and_defensive`.

- **Execution and dry run:** as [ADR 0015](../adr/0015-crdbx-write-and-ddl-primitives.md) section 4. On PostgreSQL
  `SET LOCAL lock_timeout`, one transaction for the transactional steps, then each non-transactional step alone; a
  lock timeout raises `TransientError` with the step in `details`, after a rollback. On SQLite the rebuild runs with
  `foreign_keys` off outside the transaction and `foreign_key_check` before the commit; a violation rolls back and
  raises `PlanFailed` (state `rolled_back`, details `violations` as a count).
- `database` is a layer-B `Database` the caller owns; the executor opens its own transactions (an open Tabrion
  transaction of the same thread raises `UsageError("database.nested_transaction")`). It never retries a write
  (R-NORETRY).
- **Tests:** each refusal above as a negative test, a swapped and an edited plan, drift injected between preview and
  execution, dry run leaves the fingerprint unchanged on both engines, a lock timeout, a failing non-transactional
  step reports `partial`, SQLite rebuild golden tests (R-X2).

## A.5 Guided write transaction (TAB-XW-6)

```python
class Transaction:
    def execute_checked(self, statement: Statement, params: Mapping[str, Any] | None = None, *,
                        expect_rowcount: int | None = None, max_affected: int | None = None,
                        timeout: float | None = None) -> int: ...   # 0.3, X


# module tabrion.rowedit (extra sqlalchemy)
def insert_row(table: sqlalchemy.Table, values: Mapping[str, Any]) -> sqlalchemy.Insert: ...
def update_row(table: sqlalchemy.Table, key: Mapping[str, Any], old: Mapping[str, Any],
               new: Mapping[str, Any]) -> sqlalchemy.Update: ...
def delete_row(table: sqlalchemy.Table, key: Mapping[str, Any], old: Mapping[str, Any]) -> sqlalchemy.Delete: ...
```

- `execute_checked` runs one statement and compares the affected row count: a count other than `expect_rowcount`
  or above `max_affected` raises `RowCountMismatch` and **marks the transaction failed**: every later statement raises
  `UsageError("transaction.failed")` and leaving the block rolls back, even if the caller caught the error. A
  `read()` transaction refuses it (`ReadOnlyViolation`). One of the two limits is required
  (`UsageError("transaction.limit_required")`).
- `update_row` and `delete_row` build the optimistic predicate `WHERE <pk> = :key AND <column> IS NOT DISTINCT FROM
  :old` (SQLite `IS`) for every column in `old`, with bound parameters only. A table without a primary key, or a
  `key` that is not exactly the primary key, raises `UsageError("rowedit.primary_key_required")` (no `rowid` or
  `ctid` as a write key). No upsert and no merge (specification section 1.3).
- **N-5:** `IS NOT DISTINCT FROM` on floating-point, JSON or collation-dependent columns can report a false
  conflict (safe side: nothing is written); the guide says so. Test
  `tests/unit/test_rowedit.py::test_float_and_json_old_values_may_conflict`.
- **Tests:** a concurrent change gives `RowCountMismatch` and leaves the row unchanged; caught error still rolls
  back; `max_affected` over a multi-row statement; NULL in `old`; parameter canary in errors.

## A.6 `tabrion.sqlite`: `restore` and `delete_database_file` (TAB-XW-7)

```python
@dataclass(frozen=True, slots=True)
class RestoreResult:
    path: Path
    pages: int
    integrity_check: str        # "ok" or the first message


def restore(source: str | os.PathLike[str], target: str | os.PathLike[str], *,
            overwrite: bool = False, verify: bool = True) -> RestoreResult: ...
def delete_database_file(path: str | os.PathLike[str], *, missing_ok: bool = False) -> tuple[Path, ...]: ...
```

- **`restore`:** `check_path` on both; `source` must be a SQLite database (header) and pass `integrity_check`
  before anything is written (`TabrionError("sqlite.restore_source_corrupt")`). A missing `target` is created with
  `create_private_file` (0600); an existing one needs `overwrite=True` (`FileExistsError`). The copy uses the online
  backup API from a read-only source connection into the target in one step, so the change is atomic for other
  connections of the target and works in WAL mode. With `verify`, `integrity_check` on the target afterwards. The
  same path for both raises `UsageError("sqlite.same_file")`. Restoring into a production database is the consumer's
  lock (E-X7), as is the backup of the target before a restore.
- **`delete_database_file`:** `check_path` (no symbolic link); the file must start with the SQLite header, else
  `UsageError("sqlite.not_a_database")`. It takes an exclusive lock first and raises `DatabaseInUse("sqlite.in_use")`
  when another connection holds the file; then it deletes the file and its `-wal`, `-shm` and `-journal` side files
  and returns the deleted paths. A process that opens the file after the lock check is not prevented (documented
  race; the consumer stops its own connections first).
- **N-4:** the check and the delete work through a directory file descriptor opened with `O_NOFOLLOW`; device and
  inode of the checked file are compared again right before the unlink (a swapped file is refused); side files that
  are symbolic links are only unlinked, never followed. Tests
  `tests/unit/test_sqlite_delete.py::test_swap_between_check_and_unlink_refused`,
  `::test_side_file_symlink_unlinked_not_followed`.
- **Tests:** the round trip of planning document 15 section 9.4 (backup, restore into a new file, `integrity_check`
  `ok`, schema fingerprint equal), WAL mode, a corrupt source, the 0600 mode, a held lock, the side files.

## A.7 `tabrion.postgres`: role DDL (TAB-XW-8)

```python
@dataclass(frozen=True, slots=True)
class RoleOptions:
    login: bool = False
    inherit: bool = True
    create_db: bool = False
    connection_limit: int | None = None
    valid_until: datetime | None = None
    # No field for SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, by design (ADR 0015).


@dataclass(frozen=True, slots=True)
class RolePrivileges:
    role: str
    attributes: tuple[str, ...]          # "login", "inherit", "createdb", "superuser", "bypass_rls", ...
    member_of: tuple[str, ...]           # direct memberships
    reachable: tuple[str, ...]           # every role reachable through membership (inherit or SET ROLE)
    dangerous: tuple[str, ...]           # "superuser:<role>", "bypass_rls:<role>", "createrole:<role>",
                                         # "replication:<role>", "createrole_pre16", "trigger:<table>",
                                         # "security_definer:<function>", "schema_create:<schema>"
    broad: tuple[str, ...]               # "broad:<role>" for memberships in predefined pg_* roles (finding)
    owned_objects: int
    default_privileges: int
    @property
    def safe(self) -> bool: ...          # not dangerous
    def as_dict(self) -> dict[str, object]: ...


def role_privileges(connection: psycopg.Connection, role: str) -> RolePrivileges: ...   # read-only; RoleMissing
def create_role(connection: psycopg.Connection, role: str, *, options: RoleOptions = RoleOptions(),
                password: str | None = None) -> RolePrivileges: ...
def alter_role(connection: psycopg.Connection, role: str, *, options: RoleOptions | None = None,
               password: str | None = None) -> RolePrivileges: ...
def drop_role(connection: psycopg.Connection, role: str) -> None: ...
def grant_membership(connection: psycopg.Connection, role: str, *, member: str) -> RolePrivileges: ...
def revoke_membership(connection: psycopg.Connection, role: str, *, member: str) -> RolePrivileges: ...
def grant_privileges(connection: psycopg.Connection, role: str, *,
                     privileges: Sequence[Literal["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES",
                                                  "USAGE", "CREATE", "CONNECT", "EXECUTE"]],   # no TRIGGER (K-2)
                     on: Literal["table", "sequence", "function", "schema", "database"],
                     objects: Sequence[str]) -> RolePrivileges: ...
def revoke_privileges(connection: psycopg.Connection, role: str, *, privileges: Sequence[str],
                      on: Literal["table", "sequence", "function", "schema", "database"],
                      objects: Sequence[str]) -> RolePrivileges: ...
```

- **D-1, PostgreSQL 16 and later only:** every function except `role_privileges` raises
  `UsageError("postgres.role_ddl_requires_16")` on 15 and earlier; `role_privileges` reports CREATEROLE before 16 as
  `dangerous` (`createrole_pre16`). Tests `tests/postgres/test_role_ddl.py::test_role_ddl_refused_before_16`,
  `::test_createrole_pre16_reported_dangerous`.
- **Attribute ban (E-X6):** no parameter requests SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION.
  `grant_membership` refuses (`RoleUnsafe`, reasons `reachable_*`) when `role`, or any role reachable from it, has
  one of these attributes. **N-3:** reachability counts INHERIT, the SET option and the ADMIN option. Test
  `tests/postgres/test_role_ddl.py::test_admin_option_on_dangerous_role_counts_as_reachable`.
- **D-2, no predefined roles (allowlist):** `grant_membership` refuses every role named `pg_*` or with an oid below
  `FirstNormalObjectId` (reason `predefined_role`), including `pg_read_all_data` and `pg_write_all_data`; existing
  memberships appear in `RolePrivileges.broad` as a finding. Tests
  `tests/postgres/test_role_ddl.py::test_membership_in_any_predefined_role_refused`,
  `::test_existing_predefined_membership_reported_broad`.
- **K-2, escalation without attributes:** `TRIGGER` is not grantable; `EXECUTE` on a `SECURITY DEFINER` function
  whose owner is not the grantee is refused (reason `security_definer`); `CREATE` on `public` or on a schema the
  grantee does not own is refused (reason `schema_create`, CVE-2018-1058 class); `role_privileges` reports such
  existing grants as `dangerous`. Tests `tests/postgres/test_role_ddl.py::test_trigger_privilege_not_grantable`,
  `::test_execute_on_foreign_security_definer_refused`, `::test_create_on_public_or_foreign_schema_refused`,
  `::test_role_privileges_reports_escalation_grants`. `alter_role` and `grant_privileges` refuse a target role that already has a dangerous
  attribute or reachable role (reasons `superuser`, `bypass_rls`, `createrole`, `replication`, `reachable_*`): a
  change to such a role is not a change Tabrion makes. No `WITH ADMIN OPTION`, no `WITH GRANT OPTION`.
- **Checks before every change** run in the same transaction, right before the change; the returned
  `RolePrivileges` is read after it. The consumer shows `role_privileges` in the preview (D-14).
- `drop_role` refuses a role that owns objects or has default privileges (`RoleUnsafe`, reason `owns_objects`; no
  `REASSIGN OWNED`/`DROP OWNED`) and the session user (reason `current_user`).
- Role and object names are checked against the 63-byte limit and composed with `psycopg.sql.Identifier`; `objects`
  of `table`, `sequence` and `function` are schema-qualified and resolved with `to_regclass`/`to_regprocedure`
  (`UsageError("postgres.object_missing")` when absent; a missing `role` raises `RoleMissing`).
- **Password:** hashed on the client with `PGconn.encrypt_password(..., algorithm="scram-sha-256")` and sent as the
  verifier; never in an error, `repr`, log or frame local. An empty password is refused.
- T-NOTX: these functions need a transaction of their own (as `grant_runtime_role`) and raise
  `UsageError("...nested_transaction")` inside a caller's transaction.
- **Tests:** the matrix of ADR 0015 Verification (direct, INHERIT and NOINHERIT membership, SET and ADMIN option,
  chains, every predefined role, the K-2 grants), password canaries, catalog snapshots before and after, refusals leave the catalog
  unchanged.

## A.8 `tabrion.postgres`: `create_database` and `drop_database` (TAB-XW-9)

```python
def create_database(connection: psycopg.Connection, name: str, *, owner: str | None = None,
                    template: Literal["template0", "template1"] = "template1", encoding: str = "UTF8",
                    locale: str | None = None) -> None: ...
def drop_database(connection: psycopg.Connection, name: str, *, force: bool = False) -> None: ...
def database_sessions(connection: psycopg.Connection, name: str) -> int: ...   # read-only, for the preview
```

- Both need an autocommit connection that is idle (`CREATE`/`DROP DATABASE` cannot run in a transaction); Tabrion
  never switches autocommit (T-NOTX) and raises `UsageError("postgres.autocommit_required")`.
- `create_database` checks `rolcreatedb` (or membership of `owner` for `OWNER`) first and raises
  `PermissionDenied`-classified driver errors unchanged otherwise. The name is checked against the 63-byte limit.
- `drop_database` refuses the connection's own database, `template0`, `template1` and `postgres`
  (`UsageError("postgres.database_protected")`), and a database with other sessions unless `force=True`
  (`DatabaseInUse`, details `sessions`). `force=True` uses `WITH (FORCE)` (PostgreSQL 13 and later). The backup
  before a drop and the typed name are the consumer's (D-13, E-X5).
- **Tests:** create and drop round trip, each refusal, a concurrent session, missing `CREATEDB`.

## A.9 `tabrion.profilefile`: mode `admin` (E-X8)

`ConnectionProfile.mode` becomes `Literal["read_only", "read_write", "admin"]` (`read_write` exists since 0.2).
`parse` accepts `admin`; Tabrion attaches no behavior to any mode, which stays the consumer's (write and admin
profiles, STU-XW-1). Multi-host `host` values stay refused (W-10).
