# Quickstart

This page takes five minutes. It opens a SQLite file the safe way with the driver core (layer A), then does the same
work through `tabrion.Database` (layer B). Every example on this page runs in the test suite
(`tests/docs/test_guides.py`), so what you see is what Tabrion prints.

Install Tabrion from Git first, pinned to a commit: [Installation](installation.md). Tabrion is not on PyPI.

```bash
pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"
```

## Layer A: a SQLite file with safe defaults

`tabrion.sqlite.connect` creates a new file with mode `0600` before SQLite opens it, refuses symlinks and applies the
profile: WAL, `foreign_keys=ON`, `synchronous=FULL`, a 30 second busy timeout.

```pycon
>>> from tabrion import sqlite
>>> conn = sqlite.connect("app.db")
>>> conn.execute("PRAGMA foreign_keys").fetchone()[0]
1
>>> conn.execute("PRAGMA journal_mode").fetchone()[0]
'wal'
```

The connection runs without driver-managed transactions (`isolation_level=None`). You open transactions yourself;
`write_transaction` begins with `BEGIN IMMEDIATE`, so concurrent writers wait for the lock instead of losing updates:

```pycon
>>> with sqlite.write_transaction(conn):
...     _ = conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
...     _ = conn.execute("INSERT INTO notes (body) VALUES (?)", ("first note",))
>>> with sqlite.read_transaction(conn):
...     conn.execute("SELECT id, body FROM notes").fetchall()
[(1, 'first note')]
>>> conn.close()
```

<!-- docs-test: posix -->
```pycon
>>> import os, stat
>>> oct(stat.S_IMODE(os.stat("app.db").st_mode))
'0o600'
```

## Layer B: the same through `tabrion.Database`

`Database` wraps a SQLAlchemy engine and applies the same profile to every connection of its pool. Transactions are
context managers; statements are SQL text with named parameters (`:name`).

```pycon
>>> from tabrion import Database
>>> db = Database.from_url("sqlite:///app.db")
>>> with db.transaction() as tx:
...     tx.execute("INSERT INTO notes (body) VALUES (:body)", {"body": "second note"})
1
>>> [tuple(row) for row in db.fetch_all("SELECT id, body FROM notes ORDER BY id")]
[(1, 'first note'), (2, 'second note')]
>>> db.fetch_scalar("SELECT count(*) FROM notes")
2
```

Errors come out as Tabrion exceptions with a stable code and a message that never contains parameter values:

```pycon
>>> db.execute("INSERT INTO notes (body) VALUES (:body)", {"body": None})
Traceback (most recent call last):
  ...
tabrion.errors.IntegrityViolation: database.not_null: not null violation (SQLite SQLITE_CONSTRAINT_NOTNULL, ...)
>>> db.close()
```

## Where to go next

- [SQLite](../guides/sqlite.md) and [PostgreSQL](../guides/postgresql.md): the layer-A helpers.
- [Database and Profile](../guides/database.md): layer B.
- [Errors](../guides/errors.md): classification and safe messages.
- [Transactions](../guides/transactions.md): the transaction model, retries and unknown outcomes.
