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

## Guided writes (0.3, experimental)

!!! note "Experimental since 0.3"
    `Transaction.execute_checked` and `tabrion.rowedit` are public since Tabrion 0.3 (master builds as `0.3.0.dev0`)
    and experimental (stability X): their signatures may still change within 0.3
    ([contract section 5.11](../api/contract.md#511-guided-write-transaction-since-03-experimental-tab-xw-6)). A
    consumer that requires `tabrion>=0.2,<0.3` does not have them.

A data editor writes one row the user changed and must not overwrite a change someone else made since the row was
read. `tabrion.rowedit` builds the statement with an **optimistic predicate** on the values the user saw, and
`execute_checked` runs it with a limit on the affected rows:

```pycon
>>> import sqlalchemy
>>> from tabrion import Database
>>> from tabrion.errors import RowCountMismatch
>>> from tabrion.rowedit import insert_row, update_row
>>> items = sqlalchemy.Table(
...     "items",
...     sqlalchemy.MetaData(),
...     sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
...     sqlalchemy.Column("label", sqlalchemy.String),
...     sqlalchemy.Column("price", sqlalchemy.Float),
... )
>>> db = Database.from_url("sqlite:///items.db")
>>> with db.transaction() as tx:
...     items.create(tx.connection)
...     added = tx.execute_checked(insert_row(items, {"id": 7, "label": "old", "price": 2.5}), expect_rowcount=1)
>>> seen = {"id": 7, "label": "old", "price": 2.5}  # the row as read
>>> with db.transaction() as tx:
...     tx.execute_checked(update_row(items, {"id": 7}, seen, {"label": "new"}), expect_rowcount=1)
1
>>> try:  # the same edit again: the row no longer has the values seen
...     with db.transaction() as tx:
...         tx.execute_checked(update_row(items, {"id": 7}, seen, {"label": "mine"}), expect_rowcount=1)
... except RowCountMismatch as exc:
...     print(exc.code, dict(exc.details))
transaction.rowcount_mismatch {'expected': 1, 'actual': 0}
>>> db.fetch_scalar("SELECT label FROM items WHERE id = 7")
'new'
>>> db.close()
```

The statement compares every column of `old` NULL-safe and with bound parameters only:

```sql
-- PostgreSQL
UPDATE items SET label = $1
WHERE items.id = $2
  AND (items.label COLLATE "C") IS NOT DISTINCT FROM $3
  AND items.price IS NOT DISTINCT FROM CAST($4 AS REAL)
-- SQLite
UPDATE items SET label = ? WHERE items.id = ? AND (items.label COLLATE "BINARY") IS ? AND items.price IS ?
```

If the row changed or is gone, the statement matches 0 rows: `execute_checked` raises `RowCountMismatch`
(`transaction.rowcount_mismatch`, `expected` 1, `actual` 0), nothing is written, and the transaction is failed: every
later statement raises `UsageError("transaction.failed")`, and the block rolls back even if you catch the error. Show
the user the current row and let them decide. `max_affected` limits a statement that may touch several rows
(`transaction.too_many_rows` above it); one of the two limits is required. The error carries counts only, never a
value. Pass every column you showed the user in `old`; an empty `old` is refused (`rowedit.old_required`).

**What the limits do not cover.** The count is that of your statement only: rows that a PostgreSQL rule or a trigger
(on either engine) changes as a side effect are not counted or limited and commit with it, so a preview should name
the rules and triggers of the table. And `max_affected` limits what is committed, not the work: the statement runs to
its end before the count is checked, then rolls back. The bound on the work is `timeout`.

**False conflicts (N-5).** The comparison is exact, which is the safe side: when in doubt, nothing is written.
Tabrion compares text under a binary collation (a case-insensitive collation cannot hide a change), PostgreSQL
`json` as `jsonb` (key order and blanks do not count) and a PostgreSQL `real` as `real`. What it cannot repair are
values that changed on their way through your client: a floating-point number that was rounded or formatted for
display, a SQLite JSON column whose stored text is written differently from what `json.dumps` makes of the value you
read (blanks, key order), a SQLite `NaN` (stored as `NULL`). Such a row reports a conflict on every attempt: pass the
values exactly as read, or leave such columns out of `old` (then a concurrent change of only them is not detected).
A column type without an equality operator (`xml`, `point`) cannot be in `old`, and `citext` compares
case-insensitively by its own operator.
