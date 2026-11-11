# PostgreSQL infrastructure baseline

<!-- docs-test: postgres -->

Tabrion is a library, not a service ([ADR 0001](../adr/0001-standalone-library.md)). What a PostgreSQL deployment
around it should look like is documentation, kept here: the role model, the session limits, TLS, and backup and
restore. Tabrion checks parts of it at run time (`tabrion.check`, [Diagnosis](check.md)); the rest is operations.
The binding rules are the [specification, sections 5.4 and 8](../specification.md#54-roles-and-least-privilege).

## Three roles

| Role | Does | Must not |
|---|---|---|
| **owner** | owns the schema and its tables; runs migrations (DDL) | serve application traffic |
| **runtime** | the application's DML: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, sequences, functions | be superuser, have `BYPASSRLS`, own tables, `CREATE` in the schema |
| **backup** (optional) | reads everything for logical backups: member of `pg_read_all_data` (PostgreSQL 14 or later) | write |

A setup in SQL (the passwords come from your secret store, not from a file in the repository):

```sql
CREATE ROLE inventory_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE ROLE inventory_app   LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
CREATE ROLE inventory_backup LOGIN NOSUPERUSER NOBYPASSRLS;
GRANT pg_read_all_data TO inventory_backup;

CREATE DATABASE inventory OWNER inventory_owner;
-- in the database inventory, as inventory_owner:
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA inventory AUTHORIZATION inventory_owner;
```

After every migration, the owner grants the runtime role its rights with
[`grant_runtime_role`](postgresql.md#roles) (idempotent), protecting the migration bookkeeping tables:

```python
from tabrion import postgres


def after_migrations(owner_conn, runtime_role: str, schema: str) -> None:
    report = postgres.grant_runtime_role(owner_conn, runtime_role, schema=schema, protected_tables=["alembic_version"])
    assert report.restricted, "the runtime role must not be superuser, BYPASSRLS or a table owner"
```

At start-up, the application refuses to run as anything but a runtime role: `postgres.check_role(conn)` on psycopg,
or `tabrion.check` in a readiness probe. This server's test login, for instance, is a superuser, which is exactly what
the check reports:

```pycon
>>> import os
>>> import psycopg
>>> from tabrion import postgres
>>> from tabrion.check import check
>>> conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
>>> codes = {f.code for f in check(conn)}
>>> postgres.role_info(conn).superuser == ("TAB-PG-001" in codes)
True
```

## Session limits

Every connection gets finite limits; the defaults of `SessionSettings` are a reasonable start:

| Setting | Baseline | Finding when unset |
|---|---|---|
| `statement_timeout` | 30 s for interactive work; longer per statement where needed (`timeout=`) | TAB-PG-004 |
| `lock_timeout` | 10 s | TAB-PG-005 |
| `idle_in_transaction_session_timeout` | 5 min | TAB-PG-006 |
| `transaction_timeout` | optional, PostgreSQL 17 or later | TAB-PG-012 |
| `application_name` | one per service | TAB-PG-007 |
| `TimeZone` | `UTC` | - |

Set them per connection, as Tabrion does. A role default (`ALTER ROLE ... SET`) works too, but applies only to new
sessions and is not visible in the application's code; `tabrion.check` reports what is in effect either way.

```pycon
>>> postgres.configure(conn)
>>> d = postgres.diagnose(conn)
>>> d.statement_timeout_ms, d.lock_timeout_ms, d.idle_in_transaction_timeout_ms, d.application_name
(30000, 10000, 300000, 'tabrion')
```

## Server version

PostgreSQL 16 or later (TAB-PG-008 below 16); Tabrion's CI runs 16, 17 and 18.

```pycon
>>> postgres.diagnose(conn).server_version >= 160000
True
```

## TLS

Remote connections use TLS with certificate verification: `sslmode=verify-full` and a CA in `sslrootcert`
(`Profile(tls=TLSMode.VERIFY_FULL)` in layer B, [Database and Profile](database.md#tls-for-postgresql)). Local
connections (Unix socket, loopback) may go without. `tabrion.check` reports TAB-PG-010 (remote without
`verify-full`) and TAB-PG-011 (remote without TLS).

## Backup and restore

Tabrion contains no backup tool for PostgreSQL; it only reports what it can see (`archive_mode`, `wal_level`,
finding TAB-PG-020). The baseline:

- **Point-in-time recovery** with continuous WAL archiving: pgBackRest or WAL-G, `archive_mode=on`,
  `wal_level=replica` or higher. Without it, a crash loses everything since the last dump.
- **Logical dumps** (`pg_dump -Fc` as the backup role) for moving data and for a second, independent copy; restore
  with `pg_restore`.
- **Restore drills**: restore regularly into a scratch server and run the application's checks against it. A backup
  that was never restored is not a backup.
- **Version rule:** restore only into the same or a newer PostgreSQL major version (a dump from 17 does not load
  into 16 reliably).
- Encrypt backups at rest and keep them outside the database host.

```pycon
>>> d.archive_mode in {"on", "off", "always", None}, d.wal_level in {"minimal", "replica", "logical", None}
(True, True)
>>> ("TAB-PG-020" in codes) == (d.archive_mode == "off")
True
>>> conn.close()
```

For SQLite, `tabrion.sqlite.backup` does online backups ([SQLite](sqlite.md#backups)).

!!! note "Source"
    This page condenses the PostgreSQL kit baseline of the planning (specification sections 5.4 and 8). The longer
    kit text of the platform evaluation was not part of this repository; aligning the two is TODO TB-081.
