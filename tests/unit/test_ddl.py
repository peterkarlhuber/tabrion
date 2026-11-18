"""DDL from a level-2 schema model on SQLite, and compiled PostgreSQL text (TABRION TAB-XW-3, CR-DBX).

Contract section 5.12, I-123; ADR 0015 sections 1 and 10 (D-5.3: unknown features fail closed). The round trips run
the generated text on a fresh SQLite file and compare the reflected level-2 snapshot with the model. The PostgreSQL
round trips are in ``tests/postgres/test_ddl_postgres.py``; here the PostgreSQL text is compiled without a server.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
from pathlib import Path
import sqlite3
import sys
from typing import Any, Literal

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from tabrion import Database, ddl  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import UsageError  # noqa: E402

SECRET = "SECRET-4711"

SOURCE = (
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, email TEXT, "
    "created TEXT DEFAULT CURRENT_TIMESTAMP, score REAL DEFAULT (1 + 1), CONSTRAINT uq_email UNIQUE (email))",
    "CREATE TABLE invoices (id INTEGER PRIMARY KEY, "
    "customer INTEGER NOT NULL REFERENCES customers (id) ON DELETE CASCADE, other INTEGER, "
    "total_cents INTEGER NOT NULL DEFAULT 0 CHECK (total_cents >= 0), note VARCHAR(20) DEFAULT 'x;y', "
    "UNIQUE (customer, note), CONSTRAINT ck_note CHECK (length(note) < 30 AND note <> ')'), "
    "CONSTRAINT fk_other FOREIGN KEY (other) REFERENCES customers (id) ON UPDATE SET NULL "
    "DEFERRABLE INITIALLY DEFERRED)",
    "CREATE INDEX invoices_note ON invoices (note)",
    "CREATE UNIQUE INDEX \"invoices(lower\" ON invoices (lower(note) DESC, total_cents, 'a,)' || note)",
    "CREATE INDEX partial ON invoices (note COLLATE NOCASE DESC) WHERE total_cents > 0",
    # reserved words, mixed case, blanks, a quote and non-ASCII in names; a self-reference
    'CREATE TABLE "Select" ("Group" INTEGER, "x y" REAL, "a""b" TEXT, "größe" INTEGER, '
    'PRIMARY KEY ("Group"), FOREIGN KEY ("x y") REFERENCES "Select" ("Group"))',
    'CREATE INDEX "Order" ON "Select" ("a""b", "größe" DESC)',
)


def make(path: Path, statements: tuple[str, ...] | list[str]) -> Database:
    database = Database.from_url(f"sqlite:///{path}")
    for statement in statements:
        database.execute(statement)
    return database


def take(db: Database, **options: Any) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, level=2, **options)


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Database]:
    database = make(tmp_path / "source.db", SOURCE)
    yield database
    database.close()


def run(path: Path, statements: tuple[ddl.DdlStatement, ...]) -> ts.SchemaSnapshot:
    """Each statement's text alone, as an executor would run it (one statement per ``execute``)."""
    database = make(path, [statement.sql for statement in statements])
    try:
        return take(database)
    finally:
        database.close()


def column(name: str, type_: str = "INTEGER", nullable: bool = True, default: str | None = None) -> ts.ColumnShape:
    return ts.ColumnShape(name, type_, nullable, default)


T = ts.TableShape(
    "main.t",
    (column("id", nullable=False), column("note", "TEXT"), column("n", default="0")),
    ("id",),
)
PG = dataclasses.replace(T, name="public.t")


def refused(code: str, call: Any, **details: Any) -> UsageError:
    with pytest.raises(UsageError) as info:
        call()
    assert info.value.code == code
    for key, value in details.items():
        assert info.value.details[key] == value
    assert info.value.__cause__ is None
    return info.value


# --- round trips on SQLite ------------------------------------------------------------------------------------------


def test_xw3_sqlite_round_trip(source: Database, tmp_path: Path) -> None:
    """Shape -> DDL -> a fresh database -> level-2 snapshot: the same shape and the same ``v2:`` fingerprint
    (SQLite keeps no unique constraint name, so those are ``None`` on both sides)."""
    model = take(source)
    assert [u.name for t in model.tables for u in t.unique_constraints] == [None, None, None]
    statements = ddl.create_statements(model, dialect="sqlite")
    assert all(s.transactional for s in statements)
    copy = run(tmp_path / "copy.db", statements)
    assert ts.compare(expected=model, actual=copy) == ()
    assert copy == model
    assert ts.fingerprint(copy) == ts.fingerprint(model)


def test_xw3_sqlite_statements_are_deterministic(source: Database) -> None:
    model = take(source)
    first = ddl.create_statements(model, dialect="sqlite")
    again = ddl.create_statements(ts.SchemaSnapshot.from_json(model.to_json()), dialect="sqlite")
    assert first == again
    assert [s.target for s in first] == [
        "main.Select",
        "main.customers",
        "main.invoices",
        "Order",
        "invoices(lower",
        "invoices_note",
        "partial",
    ]


def test_xw3_sqlite_golden() -> None:
    """The text SQLAlchemy compiles; identifiers quoted by the dialect where needed, the default in parentheses."""
    table = ts.TableShape(
        "main.Order",
        (column("id", nullable=False), column("select", "VARCHAR(20)", default="'a%b :c'"), column("p")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("fk", ("p",), "main.parent", ("id",), on_delete="CASCADE"),),
        unique_constraints=(ts.UniqueShape(None, ("select",)),),
        check_constraints=(ts.CheckShape("ck", "p > 0"),),
        indexes=(
            ts.IndexShape("ix", ('lower("select")', "p"), ordering=("DESC", ""), expressions=(0,), where="p > 1"),
        ),
    )
    lines = [" ".join(s.sql.split()) for s in ddl.table_statements(table, dialect="sqlite")]
    assert lines == [
        'CREATE TABLE "Order" ( id INTEGER NOT NULL, "select" VARCHAR(20) DEFAULT (\'a%b :c\'), p INTEGER, '
        'PRIMARY KEY (id), UNIQUE ("select"), CONSTRAINT ck CHECK (p > 0), '
        "CONSTRAINT fk FOREIGN KEY(p) REFERENCES parent (id) ON DELETE CASCADE )",
        'CREATE INDEX ix ON "Order" (lower("select") DESC, p) WHERE p > 1',
    ]


def test_xw3_sqlite_column_index_and_table_statements(tmp_path: Path) -> None:
    db = make(
        tmp_path / "alter.db", ["CREATE TABLE t (id INTEGER NOT NULL PRIMARY KEY, note TEXT, n INTEGER DEFAULT 0)"]
    )
    try:
        shape = take(db).tables[0]
        added = ddl.add_column_statement(shape, column("Big Note", "TEXT", default="'x'"), dialect="sqlite")
        assert added.sql == "ALTER TABLE t ADD COLUMN \"Big Note\" TEXT DEFAULT ('x')"
        assert added.target == "main.t.Big Note"
        renamed = ddl.alter_column_statements(
            shape, old=column("note", "TEXT"), new=column("memo", "TEXT"), dialect="sqlite"
        )
        index = ts.IndexShape("t_memo", ("memo",), unique=True)
        for statement in (added, *renamed):
            db.execute(statement.sql)
        shape = take(db).tables[0]
        db.execute(ddl.create_index_statement(shape, index, dialect="sqlite").sql)
        shape = take(db).tables[0]
        assert [c.name for c in shape.columns] == ["id", "memo", "n", "Big Note"]
        assert shape.columns[3].default == "'x'"
        assert shape.indexes == (index,)
        db.execute(ddl.drop_index_statement(shape, "t_memo", dialect="sqlite").sql)
        db.execute(ddl.drop_column_statement(shape, "Big Note", dialect="sqlite").sql)
        shape = take(db).tables[0]
        assert [c.name for c in shape.columns] == ["id", "memo", "n"]
        assert shape.indexes == ()
        db.execute(ddl.drop_table_statement(shape, dialect="sqlite").sql)
        assert take(db).tables == ()
    finally:
        db.close()


def test_old_and_new_are_keyword_only() -> None:
    """TB-149: ``old`` and ``new`` of ``alter_column_statements`` are keyword-only, so the two columns cannot be
    swapped by position; ``table`` stays positional."""
    old, new = column("note", "TEXT"), column("memo", "TEXT")
    with pytest.raises(TypeError, match="positional argument"):
        ddl.alter_column_statements(T, old, new, dialect="sqlite")  # type: ignore[call-arg]
    (renamed,) = ddl.alter_column_statements(T, old=old, new=new, dialect="sqlite")
    assert "RENAME" in renamed.sql and renamed.sql.index("note") < renamed.sql.index("memo")


def test_xw3_sqlite_alter_in_place_only_renames() -> None:
    """SQLite has no ALTER COLUMN and no ADD/DROP CONSTRAINT: the plan rebuilds the table (TAB-XW-4)."""
    old = column("note", "TEXT")
    for new in (
        column("note", "VARCHAR(5)"),
        column("note", "TEXT", nullable=False),
        column("note", "TEXT", default="1"),
    ):
        refused(
            "ddl.rebuild_required", lambda new=new: ddl.alter_column_statements(T, old=old, new=new, dialect="sqlite")
        )
    assert ddl.alter_column_statements(T, old=old, new=old, dialect="sqlite") == ()
    for constraint in (ts.CheckShape("ck", "n > 0"), ts.UniqueShape(None, ("n",))):
        refused(
            "ddl.rebuild_required",
            lambda constraint=constraint: ddl.add_constraint_statement(T, constraint, dialect="sqlite"),
        )
    refused("ddl.rebuild_required", lambda: ddl.drop_constraint_statement(T, "ck", dialect="sqlite"))


def test_xw3_sqlite_attached_schema_qualifies_the_index() -> None:
    shape = dataclasses.replace(T, name="aux.t")
    index = ts.IndexShape("ix", ("note",))
    assert ddl.create_index_statement(shape, index, dialect="sqlite").sql == "CREATE INDEX aux.ix ON t (note)"
    assert ddl.drop_index_statement(shape, "ix", dialect="sqlite").sql == "DROP INDEX aux.ix"
    assert ddl.table_statements(shape, dialect="sqlite")[0].sql.startswith("CREATE TABLE aux.t (")


# --- PostgreSQL text (compiled without a server) ------------------------------------------------------------------


def test_xw3_postgres_golden() -> None:
    table = ts.TableShape(
        "public.Order",
        (
            column("id", nullable=False, default="nextval('\"Order_id_seq\"'::regclass)"),
            column("note", 'TEXT COLLATE "C"', default="'a%b :c'::text"),
            column("tags", "TEXT[]"),
            column("at", "TIMESTAMP(3) WITH TIME ZONE"),
            column("p", "BIGINT"),
        ),
        ("id",),
        foreign_keys=(
            ts.ForeignKeyShape(
                "fk",
                ("p",),
                "other.parent",
                ("id",),
                on_update="SET NULL",
                deferrable=True,
                initially_deferred=True,
                match="FULL",
            ),
        ),
        unique_constraints=(ts.UniqueShape("uq", ("note",), collations=("C",)),),
        check_constraints=(ts.CheckShape("ck", "p > 0"),),
        indexes=(
            ts.IndexShape(
                "ix",
                ("lower(note)", "p", "note"),
                ordering=("DESC NULLS LAST", "", "NULLS FIRST"),
                expressions=(0,),
                collations=("C", "", ""),
                opclasses=("", "", "text_pattern_ops"),
                include=("tags",),
                where="(p > 1)",
            ),
            ts.IndexShape("gin", ("tags",), method="gin"),
        ),
    )
    lines = [" ".join(s.sql.split()) for s in ddl.table_statements(table, dialect="postgresql")]
    assert lines == [
        'CREATE TABLE public."Order" ( id INTEGER DEFAULT (nextval(\'"Order_id_seq"\'::regclass)) NOT NULL, '
        "note TEXT COLLATE \"C\" DEFAULT ('a%b :c'::text), tags TEXT[], at TIMESTAMP(3) WITH TIME ZONE, p BIGINT, "
        "PRIMARY KEY (id), CONSTRAINT uq UNIQUE (note), CONSTRAINT ck CHECK (p > 0), "
        "CONSTRAINT fk FOREIGN KEY(p) REFERENCES other.parent (id) MATCH FULL ON UPDATE SET NULL "
        "DEFERRABLE INITIALLY DEFERRED )",
        'CREATE INDEX ix ON public."Order" ((lower(note)) COLLATE "C" DESC NULLS LAST, p, '
        "note text_pattern_ops NULLS FIRST) INCLUDE (tags) WHERE ((p > 1))",
        'CREATE INDEX gin ON public."Order" USING gin (tags)',
    ]


def test_xw3_postgres_serial_for_its_own_sequence() -> None:
    """A NOT NULL integer column whose default is the sequence a serial column gets is written as a serial, so the
    table does not depend on a sequence made elsewhere; any other ``nextval`` stays a default."""
    for default in (
        "nextval('t_id_seq'::regclass)",
        "nextval('app.t_id_seq'::regclass)",
        "nextval('\"app\".t_id_seq'::regclass)",
    ):
        shape = ts.TableShape("app.t", (column("id", "BIGINT", False, default),), ("id",))
        assert (
            ddl.table_statements(shape, dialect="postgresql")[0].sql.split("\n")[1].strip() == "id BIGSERIAL NOT NULL,"
        )
    for nullable, default in ((False, "nextval('other_seq'::regclass)"), (True, "nextval('t_id_seq'::regclass)")):
        shape = ts.TableShape("app.t", (column("id", "INTEGER", nullable, default),), ())
        assert "SERIAL" not in ddl.table_statements(shape, dialect="postgresql")[0].sql


def test_xw3_postgres_cycle_is_added_after_the_tables() -> None:
    a = ts.TableShape(
        "public.a",
        (column("id", nullable=False), column("b_id")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("a_b", ("b_id",), "public.b", ("id",)),),
    )
    b = ts.TableShape(
        "public.b",
        (column("id", nullable=False), column("a_id")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("b_a", ("a_id",), "public.a", ("id",)),),
    )
    c = ts.TableShape("public.c", (column("id", nullable=False),), ("id",), indexes=(ts.IndexShape("c_id", ("id",)),))
    model = ts.SchemaSnapshot("postgresql", (a, b, c), level=2)
    statements = ddl.create_statements(model, dialect="postgresql")
    heads = [" ".join(s.sql.split()[:3]) for s in statements]
    assert heads == [
        "CREATE TABLE public.a",
        "CREATE TABLE public.b",
        "CREATE TABLE public.c",
        "ALTER TABLE public.a",
        "ALTER TABLE public.b",
        "CREATE INDEX c_id",
    ]
    assert [s.target for s in statements][3:5] == ["a_b", "b_a"]
    sqlite_model = ts.SchemaSnapshot.from_json(
        ts.SchemaSnapshot("postgresql", (a, b), level=2)
        .to_json()
        .replace("public.", "main.")
        .replace("postgresql", "sqlite")
    )
    sqlite_text = [s.sql for s in ddl.create_statements(sqlite_model, dialect="sqlite")]
    assert len(sqlite_text) == 2
    assert "REFERENCES b (id)" in sqlite_text[0]  # SQLite takes every key inline


def test_xw3_postgres_alter_statements() -> None:
    old = column("note", "VARCHAR(20)", True, "'x'::character varying")
    new = column("Memo", "TEXT", False, None)
    assert [s.sql for s in ddl.alter_column_statements(PG, old=old, new=new, dialect="postgresql")] == [
        'ALTER TABLE public.t RENAME note TO "Memo"',
        'ALTER TABLE public.t ALTER COLUMN "Memo" TYPE TEXT',
        'ALTER TABLE public.t ALTER COLUMN "Memo" SET NOT NULL',
        'ALTER TABLE public.t ALTER COLUMN "Memo" DROP DEFAULT',
    ]
    back = ddl.alter_column_statements(PG, old=new, new=column("Memo", "TEXT", True, "'y'"), dialect="postgresql")
    assert [s.sql for s in back] == [
        'ALTER TABLE public.t ALTER COLUMN "Memo" DROP NOT NULL',
        "ALTER TABLE public.t ALTER COLUMN \"Memo\" SET DEFAULT ('y')",
    ]
    assert {s.target for s in back} == {"public.t.Memo"}
    assert ddl.add_column_statement(PG, column("x", "JSONB", False, "'{}'::jsonb"), dialect="postgresql").sql == (
        "ALTER TABLE public.t ADD COLUMN x JSONB DEFAULT ('{}'::jsonb) NOT NULL"
    )
    assert ddl.drop_column_statement(PG, "note", dialect="postgresql").sql == "ALTER TABLE public.t DROP COLUMN note"
    fk = ts.ForeignKeyShape("t_n_fkey", ("n",), "public.t", ("id",), on_delete="SET DEFAULT")
    assert ddl.add_constraint_statement(PG, fk, dialect="postgresql").sql == (
        "ALTER TABLE public.t ADD CONSTRAINT t_n_fkey FOREIGN KEY(n) REFERENCES public.t (id) ON DELETE SET DEFAULT"
    )
    check = ddl.add_constraint_statement(PG, ts.CheckShape(None, "n > 0"), dialect="postgresql")
    assert (check.sql, check.target) == ("ALTER TABLE public.t ADD CHECK (n > 0)", "public.t")
    assert ddl.drop_constraint_statement(PG, "t_n_fkey", dialect="postgresql").sql == (
        "ALTER TABLE public.t DROP CONSTRAINT t_n_fkey"
    )
    assert ddl.drop_table_statement(PG, dialect="postgresql").sql == "DROP TABLE public.t"


def test_xw3_postgres_concurrently_is_not_transactional() -> None:
    index = ts.IndexShape("t_note", ("note",))
    create = ddl.create_index_statement(PG, index, dialect="postgresql", concurrently=True)
    drop = ddl.drop_index_statement(PG, "t_note", dialect="postgresql", concurrently=True)
    assert (create.sql, create.transactional) == ("CREATE INDEX CONCURRENTLY t_note ON public.t (note)", False)
    assert (drop.sql, drop.transactional) == ("DROP INDEX CONCURRENTLY public.t_note", False)
    assert ddl.create_index_statement(PG, index, dialect="postgresql").transactional
    refused(
        "ddl.unsupported_feature", lambda: ddl.create_index_statement(T, index, dialect="sqlite", concurrently=True)
    )
    with pytest.raises(TypeError):
        ddl.drop_index_statement(PG, "t_note", dialect="postgresql", concurrently=1)  # type: ignore[arg-type]


def test_xw3_unique_options_follow_the_sqlalchemy_version() -> None:
    """SQLAlchemy 2.0.30 (the floor) refuses ``postgresql_include`` on a unique constraint and drops
    ``postgresql_nulls_not_distinct`` without a word: such a shape is refused there, never generated without it."""
    from tabrion.ddl import _unique_option_renders

    for field, option, marker in (
        ("include", "postgresql_include", "INCLUDE (n)"),
        ("nulls_not_distinct", "postgresql_nulls_not_distinct", "NULLS NOT DISTINCT"),
    ):
        value: Any = ("n",) if field == "include" else True
        shape = dataclasses.replace(PG, unique_constraints=(ts.UniqueShape("uq", ("note",), **{field: value}),))
        if _unique_option_renders(option, marker.split(" (")[0]):
            assert marker in ddl.table_statements(shape, dialect="postgresql")[0].sql
        else:
            refused("ddl.unsupported_feature", lambda shape=shape: ddl.table_statements(shape, dialect="postgresql"))


# --- refusals -------------------------------------------------------------------------------------------------------


def test_xw3_level_dialect_and_argument_checks() -> None:
    level1 = ts.SchemaSnapshot("sqlite", (dataclasses.replace(T, columns=(column("id"),)),))
    refused("ddl.level_required", lambda: ddl.create_statements(level1, dialect="sqlite"))
    level2 = ts.SchemaSnapshot("sqlite", (T,), level=2)
    refused("ddl.dialect_mismatch", lambda: ddl.create_statements(level2, dialect="postgresql"))
    refused("ddl.unsupported_dialect", lambda: ddl.create_statements(level2, dialect="mysql"))  # type: ignore[arg-type]
    refused("ddl.unsupported_dialect", lambda: ddl.table_statements(T, dialect="SQLite"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ddl.create_statements(level2.to_json(), dialect="sqlite")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ddl.table_statements(T.columns[0], dialect="sqlite")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ddl.add_constraint_statement(PG, ts.IndexShape("i", ("n",)), dialect="postgresql")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("dialect", "type_"),
    [
        ("sqlite", "NULLTYPE"),  # SQLAlchemy's name for a SQLite column without a type
        ("sqlite", "INTEGER[]"),
        ("sqlite", "INTEGER; DROP TABLE t"),
        ("sqlite", "VARCHAR(20"),
        ("sqlite", "VARCHAR(1, 2, 3)"),
        ("sqlite", "VARCHAR(x)"),
        ("sqlite", "TEXT COLLATE"),
        ("sqlite", "TEXT COLLATE a b"),
        ("sqlite", "TEXT -- x"),
        ("sqlite", ""),
        ("postgresql", "mood"),  # an enum or a domain: Tabrion makes no types
        ("postgresql", "INTEGER) , evil INTEGER"),
        ("postgresql", 'TEXT COLLATE "C'),
        ("postgresql", "INTERVAL day to century"),
        ("postgresql", "SERIAL"),
    ],
)
def test_xw3_unsupported_type(dialect: Literal["sqlite", "postgresql"], type_: str) -> None:
    shape = T if dialect == "sqlite" else PG
    bad = dataclasses.replace(shape, columns=(*shape.columns, column("x", type_)))
    error = refused("ddl.unsupported_type", lambda: ddl.table_statements(bad, dialect=dialect), column="x")
    assert error.details["table"] == shape.name


@pytest.mark.parametrize(
    "type_",
    [
        "INTEGER",
        "VARCHAR(20)",
        "NUMERIC(10, 2)",
        "TIMESTAMP(3) WITH TIME ZONE",
        "TIME WITHOUT TIME ZONE",
        "DOUBLE PRECISION",
        "INTERVAL day to second",
        "BIT VARYING(5)",
        "TEXT[]",
        "INTEGER[][]",
        'TEXT COLLATE "und-x-icu"',
        "JSONB",
        "UUID",
    ],
)
def test_xw3_postgres_types_as_sqlalchemy_writes_them(type_: str) -> None:
    shape = dataclasses.replace(PG, columns=(column("x", type_),), primary_key=())
    assert f"x {type_}\n" in ddl.table_statements(shape, dialect="postgresql")[0].sql


INJECTIONS = [
    "1; DROP TABLE t",
    "1 -- x",
    "1 /* x */",
    "1) , evil INTEGER, CHECK (1",
    "(1",
    "1)",
    "1, 2",
    "'unterminated",
    '"unterminated',
    "1\x00",
]
PG_INJECTIONS = [
    "E'\\'' ; DROP TABLE t; --'",  # an escape string: the backslash hides a quote from a plain scan
    "$$'$$; DROP TABLE t; --'",  # dollar quoting
    "$1",
    "U&'\\0041'",
]


def text_shapes(shape: ts.TableShape, text: str) -> list[tuple[str, ts.TableShape]]:
    return [
        ("default", dataclasses.replace(shape, columns=(*shape.columns, column("x", default=text)))),
        ("check", dataclasses.replace(shape, check_constraints=(ts.CheckShape("ck", text),))),
        ("index_expression", dataclasses.replace(shape, indexes=(ts.IndexShape("ix", (text,), expressions=(0,)),))),
        ("index_where", dataclasses.replace(shape, indexes=(ts.IndexShape("ix", ("n",), where=text),))),
    ]


@pytest.mark.parametrize("text", INJECTIONS + PG_INJECTIONS)
def test_xw3_unsafe_sql_text_refused(text: str) -> None:
    """A default, check, expression or predicate text cannot leave its place in the statement."""
    for dialect, shape in (("sqlite", T), ("postgresql", PG)):
        if text in PG_INJECTIONS and dialect == "sqlite":
            continue
        for element, bad in text_shapes(shape, text):
            error = refused(
                "ddl.unsafe_sql_text", lambda bad=bad, dialect=dialect: ddl.table_statements(bad, dialect=dialect)
            )
            assert error.details == {"table": shape.name, "element": element}


def test_xw3_sql_text_inside_literals_and_identifiers_is_kept() -> None:
    texts = ["'a;b--c/*d)'", '"weird;)" > 0', "'it''s' <> 'x'", "length('(') = 1"]
    for text in texts:
        for _, shape in text_shapes(PG, text):
            assert text in ddl.table_statements(shape, dialect="postgresql")[-1].sql
    # SQLite: no escapes, so a backslash and $ are plain characters; [..] and `..` quote identifiers
    for text in ["'a\\'", "[a;b] IS NULL", "`a)b` IS NULL", "'$1'"]:
        for _, shape in text_shapes(T, text):
            assert text in ddl.table_statements(shape, dialect="sqlite")[-1].sql


def test_xw3_unsafe_text_error_carries_no_text() -> None:
    bad = dataclasses.replace(T, check_constraints=(ts.CheckShape("ck", f"'{SECRET}'; DROP TABLE t"),))
    error = refused("ddl.unsafe_sql_text", lambda: ddl.table_statements(bad, dialect="sqlite"))
    assert SECRET not in str(error) and SECRET not in repr(error)


def test_xw3_identifier_too_long_on_postgresql() -> None:
    ok = "a" * 63
    shape = dataclasses.replace(PG, columns=(*PG.columns, column(ok)))
    assert ok in ddl.table_statements(shape, dialect="postgresql")[0].sql
    for name in ("a" * 64, "ä" * 32):  # 64 bytes in UTF-8
        cases = [
            dataclasses.replace(PG, columns=(*PG.columns, column(name))),
            dataclasses.replace(PG, name=f"public.{name}"),
            dataclasses.replace(PG, name=f"{name}.t"),
            dataclasses.replace(PG, indexes=(ts.IndexShape(name, ("n",)),)),
            dataclasses.replace(PG, check_constraints=(ts.CheckShape(name, "n > 0"),)),
        ]
        for bad in cases:
            refused("ddl.identifier_too_long", lambda bad=bad: ddl.table_statements(bad, dialect="postgresql"))
        assert ddl.table_statements(dataclasses.replace(T, columns=(*T.columns, column(name))), dialect="sqlite")


FEATURES_PG = [
    ("exclusion_constraint", dict(exclusion_constraints=(ts.ExclusionShape("ex", "EXCLUDE USING gist (n WITH =)"),))),
    ("not_valid", dict(check_constraints=(ts.CheckShape("ck", "n > 0", validated=False),))),
    ("not_valid", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "public.t", ("id",), validated=False),))),
    ("invalid_index", dict(indexes=(ts.IndexShape("ix", ("n",), valid=False),))),
    ("index_method", dict(indexes=(ts.IndexShape("ix", ("n",), method="ivfflat"),))),
    ("match_partial", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "public.t", ("id",), match="PARTIAL"),))),
    ("unique_collation", dict(unique_constraints=(ts.UniqueShape("uq", ("note",), collations=("C",)),))),
]
FEATURES_SQLITE = [
    ("include", dict(indexes=(ts.IndexShape("ix", ("n",), include=("note",)),))),
    ("nulls_not_distinct", dict(indexes=(ts.IndexShape("ix", ("n",), nulls_not_distinct=True),))),
    ("opclass", dict(indexes=(ts.IndexShape("ix", ("n",), opclasses=("int4_ops",)),))),
    ("index_method", dict(indexes=(ts.IndexShape("ix", ("n",), method="hash"),))),
    ("nulls_order", dict(indexes=(ts.IndexShape("ix", ("n",), ordering=("NULLS FIRST",)),))),
    ("include", dict(unique_constraints=(ts.UniqueShape(None, ("n",), include=("note",)),))),
    ("nulls_not_distinct", dict(unique_constraints=(ts.UniqueShape(None, ("n",), nulls_not_distinct=True),))),
    ("deferrable_unique", dict(unique_constraints=(ts.UniqueShape(None, ("n",), deferrable=True),))),
    ("unique_collation", dict(unique_constraints=(ts.UniqueShape(None, ("note",), collations=("NOCASE",)),))),
    ("cross_schema_foreign_key", dict(foreign_keys=(ts.ForeignKeyShape(None, ("n",), "aux.p", ("id",)),))),
    ("match_full", dict(foreign_keys=(ts.ForeignKeyShape(None, ("n",), "main.p", ("id",), match="FULL"),))),
    ("unparsed_sql_text", dict(check_constraints=(ts.CheckShape(None, "CREATE TABLE t (n CHECK (n > 0))"),))),
    ("exclusion_constraint", dict(exclusion_constraints=(ts.ExclusionShape("ex", "EXCLUDE (n WITH =)"),))),
]


@pytest.mark.parametrize(("feature", "change"), FEATURES_PG)
def test_xw3_postgres_features_fail_closed(feature: str, change: dict[str, Any]) -> None:
    """ADR 0015 D-5.3: a feature that cannot be generated is refused, never left out."""
    bad = dataclasses.replace(PG, **change)
    refused("ddl.unsupported_feature", lambda: ddl.table_statements(bad, dialect="postgresql"), feature=feature)


@pytest.mark.parametrize(("feature", "change"), FEATURES_SQLITE)
def test_xw3_sqlite_features_fail_closed(feature: str, change: dict[str, Any]) -> None:
    bad = dataclasses.replace(T, **change)
    refused("ddl.unsupported_feature", lambda: ddl.table_statements(bad, dialect="sqlite"), feature=feature)


def test_xw3_postgres_unique_collation_of_the_column_type() -> None:
    """PostgreSQL takes a unique constraint's collation from the column type, so a shape that says the same is
    generated; one that differs is refused (above)."""
    shape = dataclasses.replace(
        PG,
        columns=(*PG.columns, column("c", 'TEXT COLLATE "C"')),
        unique_constraints=(ts.UniqueShape("uq", ("c", "note"), collations=("C", "")),),
    )
    assert "CONSTRAINT uq UNIQUE (c, note)" in ddl.table_statements(shape, dialect="postgresql")[0].sql


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("columns", dict(columns=())),
        ("columns", dict(columns=(column("a"), column("a")))),
        ("primary_key", dict(primary_key=("missing",))),
        ("foreign_keys", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "main.p", ()),))),
        ("foreign_keys", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("missing",), "main.p", ("id",)),))),
        ("foreign_keys", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "main.p", ("id",), on_delete="NUKE"),))),
        ("foreign_keys", dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "main.t", ("missing",)),))),
        (
            "deferrable",
            dict(foreign_keys=(ts.ForeignKeyShape("fk", ("n",), "main.p", ("id",), initially_deferred=True),)),
        ),
        ("unique_constraints", dict(unique_constraints=(ts.UniqueShape(None, ()),))),
        ("indexes", dict(indexes=(ts.IndexShape("ix", ()),))),
        ("indexes", dict(indexes=(ts.IndexShape("ix", ("missing",)),))),
        ("indexes", dict(indexes=(ts.IndexShape("ix", ("n",), expressions=(1,)),))),
        ("indexes", dict(indexes=(ts.IndexShape("ix", ("n",), ordering=("DESC", "")),))),
        ("ordering", dict(indexes=(ts.IndexShape("ix", ("n",), ordering=("DESC; DROP",)),))),
        ("name", dict(name="t")),
        ("name", dict(name="main.")),
        ("column", dict(columns=(column(""),))),
        ("column", dict(columns=(column("a\x00b"),))),
    ],
)
def test_xw3_invalid_shape(field: str, change: dict[str, Any]) -> None:
    bad = dataclasses.replace(T, **change)
    refused("ddl.invalid_shape", lambda: ddl.table_statements(bad, dialect="sqlite"), field=field)


def test_xw3_same_table_twice_in_a_model() -> None:
    model = ts.SchemaSnapshot("sqlite", (T, T), level=2)
    refused("ddl.invalid_shape", lambda: ddl.create_statements(model, dialect="sqlite"), field="name")


def test_xw3_opclass_must_be_a_plain_name() -> None:
    bad = dataclasses.replace(PG, indexes=(ts.IndexShape("ix", ("n",), opclasses=("int4_ops) ; DROP",)),))
    refused("ddl.invalid_shape", lambda: ddl.table_statements(bad, dialect="postgresql"), field="opclasses")


def test_xw3_every_shape_field_is_generated_or_refused() -> None:
    """D-5.3 hook: the fields this module knows are exactly the fields of the shapes. A field added to the model
    (triggers, policies ... in TAB-XW-4/5) fails this test until ``tabrion.ddl`` decides how to generate
    it, and until then a shape that sets it is refused at run time (next test)."""
    from tabrion.ddl import _KNOWN_FIELDS

    for cls, known in _KNOWN_FIELDS.items():
        assert {f.name for f in dataclasses.fields(cls)} == known, cls.__name__


def test_xw3_unknown_shape_field_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion import ddl as module

    known = dict(module._KNOWN_FIELDS)
    known[ts.TableShape] = known[ts.TableShape] - {"indexes"}
    known[ts.ColumnShape] = known[ts.ColumnShape] - {"default"}
    monkeypatch.setattr(module, "_KNOWN_FIELDS", known)
    assert ddl.table_statements(dataclasses.replace(T, columns=(column("id"),)), dialect="sqlite")
    with_index = dataclasses.replace(T, columns=(column("id"),), indexes=(ts.IndexShape("ix", ("id",)),))
    refused(
        "ddl.unsupported_feature",
        lambda: ddl.table_statements(with_index, dialect="sqlite"),
        feature="TableShape.indexes",
    )
    refused("ddl.unsupported_feature", lambda: ddl.table_statements(T, dialect="sqlite"), feature="ColumnShape.default")


def test_xw3_column_statements_need_alembic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Column statements use Alembic's DDL constructs (extra ``migrations``); the rest needs only SQLAlchemy."""
    monkeypatch.setitem(sys.modules, "alembic.ddl", None)
    with pytest.raises(ImportError, match=r'extra "migrations"'):
        ddl.add_column_statement(T, column("x"), dialect="sqlite")
    assert ddl.table_statements(T, dialect="sqlite")


def test_xw3_statement_dataclass() -> None:
    statement = ddl.DdlStatement("DROP TABLE t", "main.t")
    assert statement.transactional is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        statement.sql = "x"  # type: ignore[misc]
    assert "__slots__" in ddl.DdlStatement.__dict__


def test_xw3_generated_sql_runs_one_statement_at_a_time(source: Database) -> None:
    """Every text is exactly one complete statement (an executor runs each alone; no ``executescript``)."""
    from tabrion.sql import split_statements

    for statement in ddl.create_statements(take(source), dialect="sqlite"):
        assert sqlite3.complete_statement(statement.sql + ";")
        assert split_statements(statement.sql) == [statement.sql]


# --- review of #87: names with a dot (M-1), identity and generated columns (M-2) -----------------------------------


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_xw3_a_name_with_two_dots_is_refused(dialect: Literal["sqlite", "postgresql"]) -> None:
    """``s.x.y`` could be ``s."x.y"`` or ``"s.x".y``; the reviewer's probe gave ``DROP TABLE "s.x".y``, the other
    table. Every function refuses such a name, and a foreign key that refers to one."""
    dotted = dataclasses.replace(T, name="s.x.y")
    calls = [
        lambda: ddl.drop_table_statement(dotted, dialect=dialect),
        lambda: ddl.table_statements(dotted, dialect=dialect),
        lambda: ddl.add_column_statement(dotted, column("x"), dialect=dialect),
        lambda: ddl.drop_column_statement(dotted, "id", dialect=dialect),
        lambda: ddl.create_index_statement(dotted, ts.IndexShape("ix", ("id",)), dialect=dialect),
        lambda: ddl.drop_index_statement(dotted, "ix", dialect=dialect),
    ]
    for call in calls:
        refused("ddl.invalid_shape", call, table="s.x.y", field="name")
    referring = dataclasses.replace(
        T, columns=(column("id"),), foreign_keys=(ts.ForeignKeyShape("fk", ("id",), "main.x.o", ("k",)),)
    )
    refused("ddl.invalid_shape", lambda: ddl.table_statements(referring, dialect=dialect), field="name")


IDENTITY = column("id", nullable=False)


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    ("change", "feature"),
    [
        ({"identity": "ALWAYS"}, "identity_column"),
        ({"identity": "BY DEFAULT"}, "identity_column"),
        ({"generated": "STORED", "generation_expression": "id * 2"}, "generated_column"),
        ({"generated": "VIRTUAL", "generation_expression": "id * 2"}, "generated_column"),
        ({"generation_expression": "id * 2"}, "generated_column"),
    ],
)
def test_xw3_identity_and_generated_columns_are_refused(
    dialect: Literal["sqlite", "postgresql"], change: dict[str, Any], feature: str
) -> None:
    """Neither is generated yet, and a plain column in its place would have other semantics (review of #87, M-2;
    D-5.3): every function that writes a column refuses it, also as the old side of a change."""
    special = dataclasses.replace(IDENTITY, **change)
    table = dataclasses.replace(T, name="main.t", columns=(special,), primary_key=("id",))
    model = ts.SchemaSnapshot(dialect, (table,), level=2)
    plain = dataclasses.replace(T, name="main.t", columns=(IDENTITY,), primary_key=("id",))
    calls = [
        lambda: ddl.create_statements(model, dialect=dialect),
        lambda: ddl.table_statements(table, dialect=dialect),
        lambda: ddl.add_column_statement(plain, dataclasses.replace(special, name="x"), dialect=dialect),
        lambda: ddl.alter_column_statements(plain, old=IDENTITY, new=special, dialect=dialect),
        lambda: ddl.alter_column_statements(table, old=special, new=IDENTITY, dialect=dialect),
        lambda: ddl.alter_column_statements(
            table, old=special, new=dataclasses.replace(special, name="n"), dialect=dialect
        ),
    ]
    for call in calls:
        refused("ddl.unsupported_feature", call, table="main.t", feature=feature)


def test_xw4_create_statements_refuse_views() -> None:
    """Review of #89, P89-02: views are read into the level-2 snapshot, not generated."""
    table = ts.TableShape("main.t", (ts.ColumnShape("id", "INTEGER", False),), ("id",))
    model = ts.SchemaSnapshot("sqlite", (table,), level=2, views=(ts.ViewShape("main.v", "CREATE VIEW v AS SELECT 1"),))
    with pytest.raises(UsageError) as caught:
        ddl.create_statements(model, dialect="sqlite")
    assert caught.value.details == {"table": "", "feature": "view"}
