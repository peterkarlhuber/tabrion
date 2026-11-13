# Query, stream, cancel and inspect (0.2)

Tabrion 0.2 adds the methods a database tool such as Tabrion Studio needs on top of the 0.1 layer B: a materialized
`Result` with column metadata and a row cap, batches from a server-side cursor, a cancel from another thread, and a
read-only SQLAlchemy inspector. They work the same way on SQLite and PostgreSQL. Contract:
[sections 5.3 to 5.6](../api/contract.md#53-transactions) and
[implementation notes I-100..I-106](../api/contract.md#58-implementation-notes-tab-033).

| Method | Since | Stability | What it does |
|---|---|---|---|
| `Database.query`, `Transaction.query` | 0.2 | stable | one statement, at most `max_rows` rows, a frozen `Result` |
| `Transaction.stream` | 0.2 | stable | one statement in batches; PostgreSQL uses a server-side cursor |
| `Transaction.cancel` | 0.2 | stable | stops the running statement; callable from any thread |
| `Database.inspector` | 0.2 | stable | `sqlalchemy.inspect()` inside its own read transaction |
| `Database.on_begin`, `Database.run_transaction`, `Transaction.savepoint` | 0.2 | experimental | a callback at each `BEGIN`, a retry loop, a nested savepoint |

Experimental names may change in a minor release ([API stability policy](../development/api-stability.md)).

## `query` and `Result`

`Database.query` runs one statement in a read transaction of its own; inside a transaction use `Transaction.query`.
The result is fully materialized and frozen:

```pycon
>>> from tabrion import Database, Profile, Result
>>> db = Database.from_url("sqlite:///parts.db", profile=Profile(max_rows=3))
>>> db.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
0
>>> db.execute("INSERT INTO parts (name) VALUES (:name)", [{"name": f"p{i}"} for i in range(5)])
5
>>> result = db.query("SELECT id, name FROM parts ORDER BY id")
>>> isinstance(result, Result)
True
>>> [column.name for column in result.columns]
['id', 'name']
>>> result.rows
((1, 'p0'), (2, 'p1'), (3, 'p2'))
>>> result.row_count, result.truncated, result.statement_kind
(3, True, 'select')
>>> db.query("SELECT name FROM parts WHERE id = :id", {"id": 5}, max_rows=10).rows
(('p4',),)
```

`truncated` says that more rows existed than `max_rows` (the profile's `max_rows` unless the call passes its own).
On PostgreSQL `Column.type_name` is the type name of the column (`integer`, `text`, `timestamp with time zone`);
Python's `sqlite3` does not report declared types, so on SQLite it is `None` (TODO TB-122).

`query` does not use a server cursor: PostgreSQL sends every row of the result to the client before the cut. For
large results use `stream`.

### One statement, and it cannot end the transaction

`query` and `stream` take exactly one statement (on PostgreSQL through the extended query protocol, which refuses a
second command), and a statement that would end Tabrion's transaction (`COMMIT`, `ROLLBACK`, `END`, `ABORT`, with or
without `AND CHAIN`, `PREPARE TRANSACTION`) is refused before it is sent. The transaction is then broken: the block
rolls back and raises `UsageError("database.transaction_ended")`.

```pycon
>>> from tabrion.errors import UsageError
>>> try:
...     with db.transaction() as tx:
...         _ = tx.execute("INSERT INTO parts (name) VALUES ('kept?')")
...         tx.query("/* hidden */ COMMIT")
... except UsageError as exc:
...     print(exc.code)
database.transaction_ended
>>> db.query("SELECT count(*) FROM parts WHERE name = 'kept?'").rows
((0,),)
```

`Database.query` runs in `read()`, so a write fails with `ReadOnlyViolation`. Read-only is a guard, not a sandbox:
untrusted SQL needs a database role without write rights ([Security](security.md)).

## `stream`

`stream` yields lists of SQLAlchemy rows of at most `batch_size` rows. The statement runs at the first `next()`; the
generator must be used inside its transaction, and the end of the transaction closes it.

```pycon
>>> with db.read() as tx:
...     sizes = [len(batch) for batch in tx.stream("SELECT id FROM parts ORDER BY id", batch_size=2)]
>>> sizes
[2, 2, 1]
```

## `cancel`

`Transaction.cancel()` may be called from any thread. It stops the statement that is running (SQLite:
`sqlite3.Connection.interrupt()`, PostgreSQL: a cancel request); the statement raises `Canceled`, and the
transaction is not committed. It never raises and does nothing when no statement runs.

```pycon
>>> import threading
>>> from tabrion.errors import Canceled
>>> endless = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
>>> try:
...     with db.read() as tx:
...         threading.Timer(0.2, tx.cancel).start()
...         tx.query(endless)
... except Canceled as exc:
...     print(exc.classification.kind.value)
canceled
```

## `inspector`

`Database.inspector()` yields `sqlalchemy.inspect()` of a connection inside its own read transaction, so a write
through it fails, and errors are translated like every other Tabrion error.

```pycon
>>> with db.inspector() as inspector:
...     print(inspector.get_table_names(), [c["name"] for c in inspector.get_columns("parts")])
['parts'] ['id', 'name']
```

## Experimental: `on_begin`, `run_transaction`, `savepoint`

```pycon
>>> seen = []
>>> unregister = db.on_begin(lambda connection, read_only: seen.append(read_only))
>>> db.query("SELECT 1").rows
((1,),)
>>> unregister()
>>> seen
[True]
>>> def rename(tx):
...     with tx.savepoint():
...         _ = tx.execute("UPDATE parts SET name = 'first' WHERE id = 1")
...     return tx.fetch_scalar("SELECT name FROM parts WHERE id = 1")
>>> db.run_transaction(rename, attempts=3)
'first'
>>> db.close()
```

`on_begin` callbacks run inside each new transaction before the caller's block. `run_transaction` retries the whole
function for a retryable error whose outcome is known (`serialization`, `deadlock`, `lock_timeout`) with a growing
pause; an error with an unknown outcome is never retried ([Transactions](transactions.md)).
