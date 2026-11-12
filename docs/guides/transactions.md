# Transactions

The same transaction model holds in both layers and on both engines. This page explains it, shows why
`BEGIN IMMEDIATE` matters on SQLite, and gives the pattern for the one case Tabrion deliberately does not automate:
an error whose outcome is unknown. Contract: [section 3.4](../api/contract.md#34-write_transaction-read_transaction)
(layer A) and [section 5.3](../api/contract.md#53-transactions) (layer B).

## The rules

| Rule | SQLite | PostgreSQL |
|---|---|---|
| Write transaction | `BEGIN IMMEDIATE`: the write lock at once, the busy timeout applies | `BEGIN` (read committed); `repeatable_read` or `serializable` on request |
| Read transaction | deferred `BEGIN` and `query_only=ON`: a write fails with `read_only` | `BEGIN READ ONLY`: a write fails with SQLSTATE 25006 (`read_only`) |
| Nesting | refused with `UsageError` | same |
| A single autonomous statement (`db.execute`) | its own `BEGIN IMMEDIATE ... COMMIT` | its own transaction |
| End of the block | commit; any exception rolls back | same |
| Lost connection during `COMMIT` | `outcome_unknown=True`, never retried automatically | same |

| Layer A | Layer B |
|---|---|
| `tabrion.sqlite.write_transaction(conn)` | `db.transaction()` |
| `tabrion.sqlite.read_transaction(conn)` | `db.read()` |
| a psycopg connection: `conn.transaction()` (psycopg's own) | `db.transaction(isolation=...)` |

Layer-A functions that take a connection follow one more rule (T-NOTX, [ADR 0011](../adr/0011-ldp-internal-and-t-notx.md)):
**Tabrion never ends a transaction it did not open.** Called inside your transaction, its statements run in it;
called on an idle connection, it opens and ends a short transaction of its own. Functions that need a transaction of
their own (`write_transaction`, `read_transaction`, `migrate`, `grant_runtime_role`) refuse with `UsageError` instead of committing yours.

## Why `BEGIN IMMEDIATE`

A read-modify-write in a deferred SQLite transaction takes the write lock only at its first write. Two connections
can both read the old value; one of them then fails with "database is locked" or, with retries, overwrites the
other's update. `BEGIN IMMEDIATE` takes the lock before the read, so the second writer waits (up to the busy timeout)
and then reads the new value. Five threads, five increments:

```pycon
>>> import threading
>>> from tabrion import Database
>>> db = Database.from_url("sqlite:///counter.db")
>>> db.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
0
>>> db.execute("INSERT INTO counter (id, n) VALUES (1, 0)")
1
>>> def increment():
...     with db.transaction() as tx:
...         n = tx.fetch_scalar("SELECT n FROM counter WHERE id = 1")
...         tx.execute("UPDATE counter SET n = :n WHERE id = 1", {"n": n + 1})
>>> threads = [threading.Thread(target=increment) for _ in range(5)]
>>> for t in threads:
...     t.start()
>>> for t in threads:
...     t.join()
>>> db.fetch_scalar("SELECT n FROM counter WHERE id = 1")
5
```

On PostgreSQL, read committed does not protect a read-modify-write: lock the row with `SELECT ... FOR UPDATE`, or
use `isolation="serializable"` and retry on `serialization`.

## Retrying

Retry the **whole transaction**, and only when the classification says so. Tabrion 0.1 has no retry helper
(`Database.run_transaction` comes, experimental, in 0.2); a few lines do it:

```python
import random
import time

from tabrion.errors import DatabaseError


def run_with_retry(db, work, *, attempts=3, backoff=0.05):
    """Run ``work(tx)`` in a transaction; retry it only for retryable errors with a known outcome."""
    for attempt in range(1, attempts + 1):
        try:
            with db.transaction() as tx:
                return work(tx)
        except DatabaseError as exc:
            c = exc.classification
            if attempt == attempts or c is None or not c.retryable or c.outcome_unknown:
                raise
            time.sleep(backoff * 2 ** (attempt - 1) * (1 + random.random()))  # noqa: S311  # jitter, not crypto
    raise AssertionError("unreachable")
```

```pycon
>>> run_with_retry(db, lambda tx: tx.execute("UPDATE counter SET n = n + 1 WHERE id = 1"))
1
```

`retryable` is true for `locked`, `deadlock`, `serialization`, and on PostgreSQL for a lost connection whose outcome is
known (nothing was sent). It is false for constraint violations, syntax errors and permission errors: running them
again gives the same error.

## Unknown outcomes

When the connection breaks while a statement or the `COMMIT` is on its way, nobody knows whether the server
committed. `classify` reports `outcome_unknown=True` and `retryable=False`. Running the transaction again could
apply it twice (an order booked twice, a measurement counted twice).

The pattern: give every logical operation an **idempotency key** that is unique in the database, insert with
`ON CONFLICT DO NOTHING`, and read back. The syntax is the same on SQLite (3.24 or later) and PostgreSQL.

```pycon
>>> db.execute(
...     "CREATE TABLE readings (device_id TEXT NOT NULL, seq INTEGER NOT NULL, value REAL NOT NULL,"
...     " PRIMARY KEY (device_id, seq))"
... )
0
>>> def store(tx, device_id, seq, value):
...     tx.execute(
...         "INSERT INTO readings (device_id, seq, value) VALUES (:d, :s, :v) ON CONFLICT (device_id, seq) DO NOTHING",
...         {"d": device_id, "s": seq, "v": value},
...     )
...     return tx.fetch_scalar("SELECT value FROM readings WHERE device_id = :d AND seq = :s", {"d": device_id, "s": seq})
>>> with db.transaction() as tx:
...     store(tx, "sensor-7", 1, 21.5)
21.5
>>> with db.transaction() as tx:  # the same operation again, after an unknown outcome
...     store(tx, "sensor-7", 1, 21.5)
21.5
>>> db.fetch_scalar("SELECT count(*) FROM readings")
1
```

With the key in place, a retry after an unknown outcome is safe: the second run finds the row and changes nothing.
There is no upsert helper in Tabrion; the dialect `insert` of SQLAlchemy has `on_conflict_do_nothing()` on both engines (`sqlalchemy.dialects.sqlite.insert` and
`sqlalchemy.dialects.postgresql.insert`, not `sqlalchemy.insert`).

```pycon
>>> db.close()
```
