# Splitting SQL scripts

`tabrion.sql.split_statements` splits SQL text at top-level semicolons. Use it to run a migration file statement by
statement where a single multi-statement call is not possible or not wanted: statements that cannot run in a
transaction (`CREATE INDEX CONCURRENTLY`), drivers that take one statement per call, or an editor that runs the
statement under the cursor. It is pure, thread-safe and needs only the standard library. Contract:
[section 2.1](../api/contract.md#21-split_statements).

```pycon
>>> from tabrion.sql import split_statements
>>> split_statements("CREATE TABLE a (x int); INSERT INTO a VALUES (1);")
['CREATE TABLE a (x int)', 'INSERT INTO a VALUES (1)']
```

It knows PostgreSQL's quoting, so semicolons inside literals, identifiers, dollar quotes and comments do not split:

```pycon
>>> script = """
... -- seed data
... INSERT INTO notes (body) VALUES ('a; b');
... CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $body$
... BEGIN
...     NEW.updated_at := now();
...     RETURN NEW;
... END;
... $body$;
... /* outer /* nested; */ still a comment */
... SELECT 1;
... """
>>> for statement in split_statements(script):
...     print(repr(statement[:40]))
'-- seed data\nINSERT INTO notes (body) VA'
'CREATE FUNCTION touch() RETURNS trigger '
'/* outer /* nested; */ still a comment *'
```

The rules:

- recognized: `'...'` with doubled quotes, `E'...'` with backslash escapes, `"..."` identifiers, `$tag$...$tag$`,
  `--` comments and **nested** `/* ... */` comments;
- empty statements and statements that are only comments are dropped; comments inside a statement stay (the first
  statement above keeps its `-- seed data` line, and the comment before `SELECT 1` belongs to it);
- every statement is `strip()`-ped;
- it never raises: an unterminated quote or comment makes the rest of the text the last statement.

```pycon
>>> split_statements(";;  -- nothing here\n")
[]
>>> split_statements("SELECT 'unterminated; still text")
["SELECT 'unterminated; still text"]
```

The splitter is byte-identical to Axentra's migration splitter for every input that one splits correctly; the
differences are inputs Axentra split wrongly (`E'\''` and nested comments).

## Running a split script on SQLite

SQLite has no dollar quotes, which is harmless: the rules above are a superset of what SQLite accepts.

```pycon
>>> from tabrion import sqlite
>>> conn = sqlite.connect(":memory:")
>>> with sqlite.write_transaction(conn):
...     for statement in split_statements(
...         "CREATE TABLE t (v TEXT); INSERT INTO t VALUES ('x;y'); INSERT INTO t VALUES ('z')"
...     ):
...         _ = conn.execute(statement)
>>> conn.execute("SELECT v FROM t ORDER BY v").fetchall()
[('x;y',), ('z',)]
>>> conn.close()
```

Running statements one by one inside `write_transaction` keeps the all-or-nothing behavior that
`sqlite3.Connection.executescript` loses (it commits before it starts).

There is no shared checksum helper: if you record migration checksums, `hashlib.sha256(sql.encode("utf-8"))
.hexdigest()` over the file text is all you need.
