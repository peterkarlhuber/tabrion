# Using Tabrion in an existing platform

Platforms that already have their own connection handling, pools and migration runners (Axentra, Nodestro, Orkexa)
use **layer A only**, function by function, without giving up what they have
([ADR 0003](../adr/0003-existing-platforms-layer-a.md)). Nothing in layer A takes over a connection: you keep opening,
pooling and closing it, and Tabrion never ends a transaction it did not open. The mapping of the existing code to
Tabrion is [contract section 8](../api/contract.md#8-compatibility-table-existing-code-to-tabrion).

## Install by Git SHA

Tabrion is not on PyPI, and no PyPI publication is planned: consumers pin the full commit SHA of a final version
(a library through `requirements/tabrion.txt`). The rules (one Tabrion version per installation, only final versions
on a consumer's master, pre-releases only on integration branches) are in
[Installation](../getting-started/installation.md) and
[ADR 0009](../adr/0009-distribution-and-versioning.md).

## Typical first steps

| Need | Layer-A function | Replaces |
|---|---|---|
| value-free error texts in logs and API answers | `tabrion.errors.classify`, `safe_message`, `DbErrorRedactionFilter` | ad-hoc `str(exc)` |
| test fixtures for PostgreSQL schemas and roles | `tabrion.testing` | copied `conftest.py` helpers |
| session timeouts on your own pool | `tabrion.postgres.session_configurator` | a local `_configure` |
| the runtime-role start check and grants | `check_role`, `grant_runtime_role` | local role code |
| leader and migration locks | `SessionLock`, `wait_advisory_lock` | local lock loops |
| a safe SQLite file | `tabrion.sqlite.connect` with `SQLiteProfile.nodestro()` or `.orkexa()` | local connect code |
| splitting migration files | `tabrion.sql.split_statements` | a local splitter |

## Keep your behavior where you need to

Every default can be switched off to reproduce what the platform does today. `SessionSettings.unchanged()` sets
nothing; `SessionSettings(lock_timeout_ms=None, ...)` leaves single settings alone; the SQLite presets keep the
platforms' choices (`SQLiteProfile.nodestro()` has `secure_delete=False`):

```pycon
>>> from tabrion import postgres
>>> from tabrion.sqlite import SQLiteProfile
>>> postgres.SessionSettings(
...     statement_timeout_ms=30_000,
...     lock_timeout_ms=None,
...     idle_in_transaction_timeout_ms=None,
...     application_name=None,
...     timezone=None,
... )
SessionSettings(statement_timeout_ms=30000, lock_timeout_ms=None, idle_in_transaction_timeout_ms=None, transaction_timeout_ms=None, application_name=None, search_path=None, timezone=None)
>>> SQLiteProfile.nodestro().secure_delete
False
```

The one deliberate change in both SQLite presets is `foreign_keys=ON`. Run `tabrion.sqlite.foreign_key_check` on
existing files before you switch.

## Calling Tabrion inside your own transaction

A layer-A function called inside a transaction that **you** opened runs in it and leaves it open; on an idle
connection it opens and ends a short transaction of its own (T-NOTX,
[ADR 0011](../adr/0011-ldp-internal-and-t-notx.md)):

```pycon
>>> import sqlite3
>>> from tabrion import sqlite
>>> conn = sqlite.connect(":memory:")
>>> _ = conn.execute("BEGIN")
>>> _ = conn.execute("CREATE TABLE t (x)")
>>> sqlite.quick_check(conn).ok, conn.in_transaction
(True, True)
>>> _ = conn.execute("ROLLBACK")
>>> conn.close()
```

Functions that need a transaction of their own (`write_transaction`, `migrate`, `grant_runtime_role`) raise
`UsageError` instead of committing yours.

## Mapping Tabrion errors to yours

`TabrionError.code` is stable; map it by table to your own error codes. Nodestro's codes are Tabrion's with the prefix
`storage.`:

```python
from tabrion.errors import TabrionError


class PlatformError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def translate(error: TabrionError) -> PlatformError:
    return PlatformError(f"storage.{error.code.removeprefix('sqlite.')}")
```

```pycon
>>> from tabrion.errors import SchemaTooNew
>>> translate(SchemaTooNew("sqlite.schema_too_new")).code
'storage.schema_too_new'
```

## Layer B with an engine you already have

`Database.from_engine(engine)` adds Tabrion's listeners to an existing SQLAlchemy engine. They change the session of
**every** connection of that engine, the host's own included, and `close()` does not undo what they set. Give
Tabrion an engine of its own where the host must keep its settings; a read-only profile is refused for a foreign
engine with listeners.

```pycon
>>> import sqlalchemy as sa
>>> from tabrion import Database
>>> engine = sa.create_engine("sqlite:///host.db")
>>> db = Database.from_engine(engine)
>>> db.fetch_scalar("PRAGMA foreign_keys")
1
>>> db.close()
>>> engine.dispose()  # the engine stays yours
```
