# Portability between SQLite and PostgreSQL

Tabrion gives you **rules and executable checks, not a type library**. If your schema follows the rules below, the
same SQLAlchemy table definitions and the same application code behave alike on SQLite and PostgreSQL.
`tabrion.testing.profiles.portability_cases()` checks each rule against either engine (TAB-T-D01..D09,
[Testing](testing.md#the-portability-cases)). Specification: [section 6](../specification.md#6-data-type-portability-sqlite-and-postgresql-e).

| Data | Rule | SQLite pitfall | PostgreSQL |
|---|---|---|---|
| Timestamps (D01) | always UTC, `datetime` with `tzinfo=UTC`; on SQLite ISO 8601 text ending in `Z` | no time type; `DateTime(timezone=True)` comes back naive | `timestamptz`, session `TimeZone=UTC` |
| Money, decimals (D02) | `Decimal`; `Numeric(p, s, asdecimal=True)`, on SQLite stored as TEXT or as integer cents | `NUMERIC` goes through `float` | `numeric(p, s)` |
| Integer IDs (D03) | `BigInteger().with_variant(Integer, "sqlite")` | only exactly `INTEGER PRIMARY KEY` is a `rowid` alias | `bigint generated always as identity` |
| UUID (D04) | 36-character text on SQLite, `uuid` on PostgreSQL | - | `uuid` |
| Boolean (D05) | `Boolean(create_constraint=True)` | without the CHECK any number is accepted | `boolean` |
| Text with a length (D06) | `CHECK(length(x) <= n)` | `VARCHAR(n)` is not enforced | enforced |
| JSON (D07) | `JSON().with_variant(JSONB, "postgresql")` | no type check | `jsonb` |
| Foreign keys (D08) | always declare them; the profile turns `foreign_keys` on | off by default | enforced |
| Measurements (D09) | `Double`, the unit in a column of its own, no NaN or infinity | - | `double precision` |

## A portable table

The examples of this section run twice in the test suite: on SQLite and on PostgreSQL, with `DATABASE_URL` naming
the database.

<!-- docs-test: sqlite-and-postgres -->

With the mapping above, an application would read a `str` on SQLite and a `datetime` or `uuid.UUID` on PostgreSQL
for the timestamp and UUID columns. Tabrion ships no type library; two small `TypeDecorator`s give the same Python
type on both engines and store the forms of D01 and D04:

```python
import datetime as dt
from decimal import Decimal
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

_UTC_TEXT = "%Y-%m-%dT%H:%M:%S.%fZ"


class UTCDateTime(sa.types.TypeDecorator):
    """An aware UTC ``datetime`` in Python; ISO 8601 text with ``Z`` on SQLite, ``timestamptz`` on PostgreSQL."""

    impl = sa.String(32)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.DateTime(timezone=True))
        return dialect.type_descriptor(sa.String(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware UTC value")
        value = value.astimezone(dt.UTC)
        return value if dialect.name == "postgresql" else value.strftime(_UTC_TEXT)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return dt.datetime.strptime(value, _UTC_TEXT).replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


class UUIDText(sa.types.TypeDecorator):
    """A ``uuid.UUID`` in Python; 36-character text on SQLite, ``uuid`` on PostgreSQL."""

    impl = sa.String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.Uuid(as_uuid=True))
        return dialect.type_descriptor(sa.String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


metadata = sa.MetaData()

orders = sa.Table(
    "orders",
    metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.Identity(always=True), primary_key=True),
    sa.Column("public_id", UUIDText(), nullable=False, unique=True),
    sa.Column("created_at", UTCDateTime(), nullable=False),
    sa.Column("total_cents", sa.BigInteger, nullable=False),
    sa.Column("paid", sa.Boolean(create_constraint=True), nullable=False),
    sa.Column("note", sa.Text, sa.CheckConstraint("length(note) <= 80"), nullable=True),
    sa.Column("payload", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
)
```

```pycon
>>> import os
>>> from tabrion import Database
>>> db = Database.from_env("DATABASE_URL")
>>> metadata.create_all(db.engine)
>>> order_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
>>> with db.transaction() as tx:
...     _ = tx.execute(
...         orders.insert(),
...         {
...             "public_id": order_id,
...             "created_at": dt.datetime(2026, 11, 8, 9, 30, tzinfo=dt.UTC),
...             "total_cents": int(Decimal("19.90") * 100),
...             "paid": True,
...             "note": "first",
...             "payload": {"items": 2},
...         },
...     )
>>> row = db.fetch_one(sa.select(orders))
>>> row.id, row.public_id == order_id, row.created_at.isoformat(), Decimal(row.total_cents) / 100
(1, True, '2026-11-08T09:30:00+00:00', Decimal('19.9'))
>>> row.paid, row.payload
(True, {'items': 2})
>>> stored = db.fetch_scalar("SELECT created_at FROM orders")  # what the database holds
>>> stored == ("2026-11-08T09:30:00.000000Z" if db.dialect == "sqlite" else dt.datetime(2026, 11, 8, 9, 30, tzinfo=dt.UTC))
True
```

The row count of a SQLAlchemy Core `insert()` is not portable. Since SQLAlchemy 2.0.28 a result keeps the driver's
`rowcount` only for `UPDATE` and `DELETE`, unless the statement carries the `preserve_rowcount=True` execution option.
On PostgreSQL SQLAlchemy then reports `-1` for every Core insert (with or without a generated key, and for a list of
parameter sets), and `execute` turns that into `0`; SQLite still reports the true count. SQL text inserts and Core
`update()`/`delete()` report the true count on both engines. Set the option when you need the count (TODO TB-080):

```pycon
>>> row = {"created_at": dt.datetime.now(dt.UTC), "total_cents": 5, "paid": False, "note": None, "payload": {}}
>>> counting_insert = orders.insert().execution_options(preserve_rowcount=True)
>>> with db.transaction() as tx:
...     plain = tx.execute(orders.insert(), {**row, "public_id": uuid.uuid4()})
...     counted = tx.execute(counting_insert, {**row, "public_id": uuid.uuid4()})
>>> plain == (1 if db.dialect == "sqlite" else 0), counted
(True, 1)
>>> db.execute(orders.delete().where(orders.c.total_cents == 5))
2
```

The `CHECK` holds on both engines; without it, SQLite would store any length:

```pycon
>>> db.execute(
...     orders.insert(),
...     {
...         "public_id": uuid.uuid4(),
...         "created_at": dt.datetime.now(dt.UTC),
...         "total_cents": 1,
...         "paid": False,
...         "note": "x" * 81,
...         "payload": {},
...     },
... )
Traceback (most recent call last):
  ...
tabrion.errors.IntegrityViolation: database.check: check violation ...
```

## Checking the rules

```pycon
>>> from tabrion.testing import profiles
>>> for case in profiles.portability_cases():
...     case.check(db)
...     print(case.id)
TAB-T-D01
TAB-T-D02
TAB-T-D03
TAB-T-D04
TAB-T-D05
TAB-T-D06
TAB-T-D07
TAB-T-D08
TAB-T-D09
>>> metadata.drop_all(db.engine)
>>> db.close()
```

On SQLite only D03, D05, D06 and D08 can fail because of an engine setting; the others are executable examples of
the mapping. On PostgreSQL every case checks the server (time zone, `numeric`, identity, `uuid`, constraints,
`jsonb`, foreign keys, float digits).

## SQL text

- Use named parameters (`:name`); write `CAST(:x AS int)` instead of `:x::int`.
- `INSERT ... ON CONFLICT (...) DO NOTHING` and `RETURNING` work on both (SQLite 3.24 and 3.35 or later).
- Prefer SQLAlchemy Core expressions where the dialects differ (`func.now()`, string concatenation, `LIMIT` with
  `OFFSET`).
