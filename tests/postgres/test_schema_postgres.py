"""Schema fingerprint and comparison on PostgreSQL (TABRION TAB-CR-2, contract section 5.10, I-110)."""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.testing import PostgresSchema  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402


@pytest.fixture
def names(tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    name = tabrion_postgres_schema.name
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE TABLE {name}.invoices (id bigint PRIMARY KEY, total_cents integer NOT NULL, "
            f"note varchar(20), tags text[], at timestamptz)"
        )
        conn.execute(f"CREATE TABLE {name}.lines (invoice bigint, n int, PRIMARY KEY (invoice, n))")
    return name


@pytest.fixture
def db(tabrion_postgres_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(tabrion_postgres_url))
    yield database
    database.close()


def take(db: Database, **options: Any) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, **options)


def test_cr2_postgres_snapshot(db: Database, names: str) -> None:
    snap = take(db, schemas=[names])
    assert snap.dialect == "postgresql"
    assert [t.name for t in snap.tables] == [f"{names}.invoices", f"{names}.lines"]
    invoices = snap.table(f"{names}.invoices")
    assert invoices is not None
    assert [(c.name, c.type, c.nullable) for c in invoices.columns] == [
        ("id", "BIGINT", False),
        ("total_cents", "INTEGER", False),
        ("note", "VARCHAR(20)", True),
        ("tags", "TEXT[]", True),
        ("at", "TIMESTAMP WITH TIME ZONE", True),
    ]
    lines = snap.table(f"{names}.lines")
    assert lines is not None and lines.primary_key == ("invoice", "n")
    assert take(db, tables=[f"{names}.lines", f"{names}.invoices"]) == snap


def test_cr2_postgres_drift(db: Database, names: str, tabrion_postgres_url: str) -> None:
    expected = take(db, schemas=[names])
    print_before = ts.fingerprint(expected)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {names}.invoices DROP COLUMN total_cents")
        conn.execute(f"ALTER TABLE {names}.invoices ALTER COLUMN note TYPE varchar(40)")
        conn.execute(f"ALTER TABLE {names}.invoices ALTER COLUMN at SET NOT NULL")
    actual = take(db, schemas=[names])
    assert ts.fingerprint(actual) != print_before
    assert [str(d) for d in ts.compare(expected, actual)] == [
        f"column_missing: {names}.invoices.total_cents",
        f"column_type: {names}.invoices.note",
        f"column_nullable: {names}.invoices.at",
    ]


def test_cr2_postgres_views_are_left_out(db: Database, names: str, tabrion_postgres_url: str) -> None:
    # review of #64, L-4 a: has_table is true for a view and a materialized view on PostgreSQL.
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        schema = psycopg.sql.Identifier(names)
        for kind, view in (("VIEW", "v"), ("MATERIALIZED VIEW", "mv")):
            conn.execute(
                psycopg.sql.SQL("CREATE {} {}.{} AS SELECT id FROM {}.invoices").format(
                    psycopg.sql.SQL(kind), schema, psycopg.sql.Identifier(view), schema
                )
            )
    snap = take(db, tables=[f"{names}.v", f"{names}.mv", f"{names}.lines"])
    assert [t.name for t in snap.tables] == [f"{names}.lines"]


# --- level 2 (TABRION TAB-XW-2, contract section 5.10, I-111) ---------------------------------------------------------


@pytest.fixture
def constrained(tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    name = tabrion_postgres_schema.name
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE TABLE {name}.customers (id bigserial PRIMARY KEY, name text NOT NULL UNIQUE, "
            f"created timestamptz DEFAULT now(), CONSTRAINT uq_created UNIQUE (created, name))"
        )
        conn.execute(
            f"CREATE TABLE {name}.invoices (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            f"customer bigint NOT NULL REFERENCES {name}.customers (id) ON DELETE CASCADE, other bigint, "
            f"total_cents integer NOT NULL DEFAULT 0 CHECK (total_cents >= 0), note varchar(20) DEFAULT 'x', "
            f"CONSTRAINT ck_note CHECK (length(note) < 30), "
            f"CONSTRAINT fk_other FOREIGN KEY (other) REFERENCES {name}.customers (id) ON UPDATE SET NULL)"
        )
        conn.execute(f"CREATE INDEX invoices_note ON {name}.invoices (note)")
        conn.execute(f"CREATE UNIQUE INDEX invoices_lower ON {name}.invoices (lower(note), total_cents)")
    return name


def test_xw2_postgres_default_level_is_1(db: Database, constrained: str) -> None:
    snap = take(db, schemas=[constrained])
    assert snap.level == 1 and ts.fingerprint(snap).startswith("v1:sha256:")
    assert all(not t.foreign_keys and not t.indexes and all(c.default is None for c in t.columns) for t in snap.tables)


def test_xw2_postgres_level2_shapes(db: Database, constrained: str) -> None:
    name = constrained
    snap = take(db, schemas=[name], level=2)
    assert snap.level == 2 and ts.fingerprint(snap).startswith("v2:sha256:")
    customers, invoices = snap.table(f"{name}.customers"), snap.table(f"{name}.invoices")
    assert customers is not None and invoices is not None
    assert [(c.name, c.default) for c in customers.columns] == [
        ("id", f"nextval('{name}.customers_id_seq'::regclass)"),
        ("name", None),
        ("created", "now()"),
    ]
    assert customers.unique_constraints == (
        ts.UniqueShape("uq_created", ("created", "name")),
        ts.UniqueShape("customers_name_key", ("name",)),
    )
    assert customers.indexes == ()  # the indexes of the constraints are not indexes of the shape
    assert [(c.name, c.default) for c in invoices.columns] == [
        ("id", None),  # an identity column has no default
        ("customer", None),
        ("other", None),
        ("total_cents", "0"),
        ("note", "'x'::character varying"),
    ]
    assert invoices.foreign_keys == (
        ts.ForeignKeyShape("invoices_customer_fkey", ("customer",), f"{name}.customers", ("id",), on_delete="CASCADE"),
        ts.ForeignKeyShape("fk_other", ("other",), f"{name}.customers", ("id",), on_update="SET NULL"),
    )
    assert invoices.check_constraints == (
        ts.CheckShape("ck_note", "length(note::text) < 30"),
        ts.CheckShape("invoices_total_cents_check", "total_cents >= 0"),
    )
    assert invoices.indexes == (
        ts.IndexShape("invoices_lower", ("lower(note::text)", "total_cents"), unique=True),
        ts.IndexShape("invoices_note", ("note",)),
    )
    assert ts.SchemaSnapshot.from_json(snap.to_json()) == snap


def test_xw2_postgres_level2_drift(db: Database, constrained: str, tabrion_postgres_url: str) -> None:
    name = constrained
    expected = take(db, schemas=[name], level=2)
    level1 = ts.fingerprint(take(db, schemas=[name]))
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {name}.invoices ALTER COLUMN total_cents SET DEFAULT 1")
        conn.execute(f"ALTER TABLE {name}.invoices DROP CONSTRAINT fk_other")
        conn.execute(
            f"ALTER TABLE {name}.invoices ADD CONSTRAINT fk_other FOREIGN KEY (other) REFERENCES {name}.customers (id)"
        )
        conn.execute(f"ALTER TABLE {name}.invoices DROP CONSTRAINT ck_note")
        conn.execute(f"ALTER TABLE {name}.invoices ADD CONSTRAINT uq_note UNIQUE (note)")
        conn.execute(f"DROP INDEX {name}.invoices_lower")
        conn.execute(f"CREATE INDEX invoices_other ON {name}.invoices (other)")
    actual = take(db, schemas=[name], level=2)
    assert ts.fingerprint(take(db, schemas=[name])) == level1  # level 1 sees none of it
    assert ts.fingerprint(actual) != ts.fingerprint(expected)
    differences = ts.compare(expected, actual)
    assert [str(d) for d in differences] == [
        f"column_default: {name}.invoices.total_cents",
        f"foreign_key_missing: {name}.invoices.other (fk_other)",
        f"foreign_key_added: {name}.invoices.other (fk_other)",
        f"unique_added: {name}.invoices.note (uq_note)",
        f"check_missing: {name}.invoices (ck_note)",
        f"index_missing: {name}.invoices (invoices_lower)",
        f"index_added: {name}.invoices.other (invoices_other)",
    ]
    assert [d.breaking for d in differences] == [True, True, True, True, True, True, False]


def test_xw2_postgres_foreign_key_to_the_default_schema(
    db: Database, constrained: str, tabrion_postgres_url: str
) -> None:
    # SQLAlchemy reports no referred schema for a table found through search_path; the shape names the default schema.
    name = constrained
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(f"CREATE TABLE public.{name}_ref (id bigint PRIMARY KEY)")
        try:
            conn.execute(f"CREATE TABLE {name}.refs (ref bigint CONSTRAINT fk_ref REFERENCES public.{name}_ref (id))")
            refs = take(db, tables=[f"{name}.refs"], level=2).table(f"{name}.refs")
        finally:
            conn.execute(f"DROP TABLE public.{name}_ref CASCADE")
    assert refs is not None
    assert refs.foreign_keys == (ts.ForeignKeyShape("fk_ref", ("ref",), f"public.{name}_ref", ("id",)),)


# Detail added after the review of the draft (open question 1): a change only in one attribute changes the v2
# fingerprint and is named by compare; level 1 does not see it. {s} is the schema fixture's generated name.
DETAIL_BASE = (
    "CREATE TABLE {s}.p (id bigint PRIMARY KEY)",
    "CREATE TABLE {s}.t (a bigint, b text, c int, CONSTRAINT fk FOREIGN KEY (a) REFERENCES {s}.p (id), "
    "CONSTRAINT u UNIQUE (a, b))",
    "CREATE INDEX ix ON {s}.t (a, b)",
    "CREATE UNIQUE INDEX ux ON {s}.t (b, c)",
)


@pytest.mark.parametrize(
    ("statements", "kinds", "check"),
    [
        (
            ["DROP INDEX {s}.ix", "CREATE INDEX ix ON {s}.t (a DESC, b)"],
            ["index_missing", "index_added"],
            ("ix", "ordering", ("DESC", "")),
        ),
        (
            ["DROP INDEX {s}.ix", "CREATE INDEX ix ON {s}.t (a DESC NULLS LAST, b NULLS FIRST)"],
            ["index_missing", "index_added"],
            ("ix", "ordering", ("DESC NULLS LAST", "NULLS FIRST")),
        ),
        (
            ["DROP INDEX {s}.ix", "CREATE INDEX ix ON {s}.t (a, b) WHERE a > 0"],
            ["index_missing", "index_added"],
            ("ix", "where", "(a > 0)"),
        ),
        (
            ["DROP INDEX {s}.ix", "CREATE INDEX ix ON {s}.t (a, b) INCLUDE (c)"],
            ["index_missing", "index_added"],
            ("ix", "include", ("c",)),
        ),
        (
            ["DROP INDEX {s}.ux", "CREATE UNIQUE INDEX ux ON {s}.t (b, c) NULLS NOT DISTINCT"],
            ["index_missing", "index_added"],
            ("ux", "nulls_not_distinct", True),
        ),
        (
            ["ALTER TABLE {s}.t DROP CONSTRAINT u", "ALTER TABLE {s}.t ADD CONSTRAINT u UNIQUE (a, b) INCLUDE (c)"],
            ["unique_missing", "unique_added"],
            ("u", "include", ("c",)),
        ),
        (
            [
                "ALTER TABLE {s}.t DROP CONSTRAINT u",
                "ALTER TABLE {s}.t ADD CONSTRAINT u UNIQUE NULLS NOT DISTINCT (a, b)",
            ],
            ["unique_missing", "unique_added"],
            ("u", "nulls_not_distinct", True),
        ),
        (
            ["ALTER TABLE {s}.t DROP CONSTRAINT u", "ALTER TABLE {s}.t ADD CONSTRAINT u UNIQUE (a, b) DEFERRABLE"],
            ["unique_missing", "unique_added"],
            ("u", "deferrable", True),
        ),
        (
            [
                "ALTER TABLE {s}.t DROP CONSTRAINT u",
                "ALTER TABLE {s}.t ADD CONSTRAINT u UNIQUE (a, b) DEFERRABLE INITIALLY DEFERRED",
            ],
            ["unique_missing", "unique_added"],
            ("u", "initially_deferred", True),
        ),
        (
            ["ALTER TABLE {s}.t ALTER CONSTRAINT fk DEFERRABLE"],
            ["foreign_key_missing", "foreign_key_added"],
            ("fk", "deferrable", True),
        ),
        (
            ["ALTER TABLE {s}.t ALTER CONSTRAINT fk DEFERRABLE INITIALLY DEFERRED"],
            ["foreign_key_missing", "foreign_key_added"],
            ("fk", "initially_deferred", True),
        ),
    ],
)
def test_xw2_postgres_detail_changes_the_v2_fingerprint(
    db: Database,
    tabrion_postgres_url: str,
    tabrion_postgres_schema: PostgresSchema,
    statements: list[str],
    kinds: list[str],
    check: tuple[str, str, Any],
) -> None:
    name = tabrion_postgres_schema.name
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        for statement in DETAIL_BASE:
            conn.execute(statement.format(s=name))
    before1, before2 = take(db, schemas=[name]), take(db, schemas=[name], level=2)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement.format(s=name))
    after1, after2 = take(db, schemas=[name]), take(db, schemas=[name], level=2)
    assert ts.fingerprint(after1) == ts.fingerprint(before1)  # level 1 does not see it
    assert ts.fingerprint(after2) != ts.fingerprint(before2)
    assert [d.kind for d in ts.compare(before2, after2)] == kinds
    table = after2.table(f"{name}.t")
    assert table is not None
    shapes = {s.name: s for s in (*table.indexes, *table.unique_constraints, *table.foreign_keys)}
    target, field, value = check
    assert getattr(shapes[target], field) == value
    assert ts.SchemaSnapshot.from_json(after2.to_json()) == after2
