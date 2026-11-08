"""TAB-011: ``tabrion.sql.split_statements`` (contract section 2.1, TAB-T-Q01..Q10).

Identity with Axentra is checked against ``tests/golden/axentra_splitter.py`` (Axentra's splitter, verbatim) and
``tests/golden/axentra_splitter.json`` (its output for every SQL file Axentra ships). The documented differences are
checked in both directions: Tabrion splits them right and the Axentra oracle splits them wrong, so a vector that does
not exercise a difference cannot pass for one (negative probe).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
import pytest

from tabrion.sql import split_statements
from tests.golden.axentra_splitter import AXENTRA_COMMIT, axentra_split_statements

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "axentra_splitter.json"


def _golden() -> dict[str, object]:
    data: dict[str, object] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data


# --- TAB-T-Q01: Axentra's own test vector ----------------------------------------------------------------------------

AXENTRA_VECTOR = """-- head; comment
CREATE TABLE a (v text DEFAULT 'x;y');
/* block; comment */ CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $body$ SELECT 1; $body$;
SELECT "odd;name" FROM a;
"""


def test_q01_axentra_vector() -> None:
    parts = split_statements(AXENTRA_VECTOR)
    assert parts == [
        "-- head; comment\nCREATE TABLE a (v text DEFAULT 'x;y')",
        "/* block; comment */ CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $body$ SELECT 1; $body$",
        'SELECT "odd;name" FROM a',
    ]
    assert parts == axentra_split_statements(AXENTRA_VECTOR)
    assert split_statements("-- only a comment\n") == []


# --- TAB-T-Q02: every SQL file Axentra ships, byte for byte ----------------------------------------------------------


def test_q02_golden_file_matches_the_oracle() -> None:
    """The golden file and the oracle agree, so neither can drift alone."""
    data = _golden()
    assert data["axentra_commit"] == AXENTRA_COMMIT
    files = data["files"]
    assert isinstance(files, list)
    assert len(files) >= 14
    for entry in files:
        assert hashlib.sha256(entry["sql"].encode("utf-8")).hexdigest() == entry["sha256"], entry["path"]
        assert axentra_split_statements(entry["sql"]) == entry["statements"], entry["path"]


def test_q02_byte_identical_for_every_axentra_sql_file() -> None:
    files = _golden()["files"]
    assert isinstance(files, list)
    shipped = [e for e in files if e["path"].startswith("src/axentra/data/sql/")]
    assert len(shipped) == 14
    for entry in files:
        result = split_statements(entry["sql"])
        assert result == entry["statements"], entry["path"]
        # Byte identity, not just equal strings after a normalization.
        assert [s.encode("utf-8") for s in result] == [s.encode("utf-8") for s in entry["statements"]]


# --- TAB-T-Q03..Q07: quoting, comments, function bodies --------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT $$a;b$$; SELECT 2", ["SELECT $$a;b$$", "SELECT 2"]),
        ("SELECT $tag$a;$x$;b$tag$; SELECT 2", ["SELECT $tag$a;$x$;b$tag$", "SELECT 2"]),
        ("SELECT $_t$;$_t$; SELECT 2", ["SELECT $_t$;$_t$", "SELECT 2"]),
        ("SELECT $Ä$;$Ä$; SELECT 2", ["SELECT $Ä$;$Ä$", "SELECT 2"]),
        ("SELECT $1, $2; SELECT 3", ["SELECT $1, $2", "SELECT 3"]),
        ("SELECT $body$ never closed; SELECT 2", ["SELECT $body$ never closed; SELECT 2"]),
    ],
)
def test_q03_dollar_tags(sql: str, expected: list[str]) -> None:
    assert split_statements(sql) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT E'it\\'s; fine'; SELECT 2", ["SELECT E'it\\'s; fine'", "SELECT 2"]),
        ("SELECT e'\\\\'; SELECT 2", ["SELECT e'\\\\'", "SELECT 2"]),
        ("SELECT E'a''b;c'; SELECT 2", ["SELECT E'a''b;c'", "SELECT 2"]),
        # Not an escape string: the E belongs to an identifier, so the backslash is an ordinary character.
        ("SELECT name'x\\'; SELECT 2", ["SELECT name'x\\'", "SELECT 2"]),
        ("SELECT 'it''s; fine'; SELECT 2", ["SELECT 'it''s; fine'", "SELECT 2"]),
        ("SELECT '\\'; SELECT 2", ["SELECT '\\'", "SELECT 2"]),
        ('SELECT "a""b;c"; SELECT 2', ['SELECT "a""b;c"', "SELECT 2"]),
        ("SELECT 'never closed; SELECT 2", ["SELECT 'never closed; SELECT 2"]),
    ],
)
def test_q04_quotes_and_escape_strings(sql: str, expected: list[str]) -> None:
    assert split_statements(sql) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 /* a /* b; */ c; */; SELECT 2", ["SELECT 1 /* a /* b; */ c; */", "SELECT 2"]),
        ("SELECT /* /* /* ; */ */ */ 1; SELECT 2", ["SELECT /* /* /* ; */ */ */ 1", "SELECT 2"]),
        ("SELECT 1 /*/ ; */; SELECT 2", ["SELECT 1 /*/ ; */", "SELECT 2"]),
        # Unterminated: the rest is the last statement, and the server reports it.
        ("SELECT 1; /* open; SELECT 2", ["SELECT 1", "/* open; SELECT 2"]),
    ],
)
def test_q05_nested_block_comments(sql: str, expected: list[str]) -> None:
    assert split_statements(sql) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 -- no; split here\n; SELECT 2", ["SELECT 1 -- no; split here", "SELECT 2"]),
        ("SELECT 1 /* no; split */; SELECT 2", ["SELECT 1 /* no; split */", "SELECT 2"]),
        ("-- a; b\n-- c; d\nSELECT 1", ["-- a; b\n-- c; d\nSELECT 1"]),
        ("SELECT 1; -- trailing; comment", ["SELECT 1"]),
        ("SELECT 1 -- old Mac line end\r; SELECT 2", ["SELECT 1 -- old Mac line end", "SELECT 2"]),
        ("SELECT 1 -- CRLF\r\n; SELECT 2", ["SELECT 1 -- CRLF", "SELECT 2"]),
        ("SELECT 1-- 2; SELECT 3", ["SELECT 1-- 2; SELECT 3"]),
    ],
)
def test_q06_semicolon_in_comment(sql: str, expected: list[str]) -> None:
    assert split_statements(sql) == expected


FUNCTION = """CREATE OR REPLACE FUNCTION touch() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.updated_at := now(); -- keep; this
    RAISE NOTICE 'a;b';
    RETURN NEW;
END;
$fn$;
CREATE TRIGGER t BEFORE UPDATE ON items FOR EACH ROW EXECUTE FUNCTION touch();
DO $$ BEGIN PERFORM 1; END $$;"""


def test_q07_create_function_body() -> None:
    parts = split_statements(FUNCTION)
    assert len(parts) == 3
    assert parts[0].startswith("CREATE OR REPLACE FUNCTION") and parts[0].endswith("$fn$")
    assert "RAISE NOTICE 'a;b';" in parts[0]
    assert parts[1].startswith("CREATE TRIGGER") and parts[2] == "DO $$ BEGIN PERFORM 1; END $$"


# --- TAB-T-Q08: empty input and comment-only statements --------------------------------------------------------------


@pytest.mark.parametrize(
    "sql", ["", "   ", ";", ";;\n;", "-- c", "-- a\n-- b\n", "/* c */", "/* a */ -- b\n;", "\u00a0;\u2003", "\n\r\t"]
)
def test_q08_empty_and_comment_only_input(sql: str) -> None:
    assert split_statements(sql) == []


def test_q08_results_are_stripped_and_never_empty() -> None:
    assert split_statements("  SELECT 1  ;\n\n  SELECT 2\n") == ["SELECT 1", "SELECT 2"]
    assert split_statements("SELECT 1;;;SELECT 2;") == ["SELECT 1", "SELECT 2"]


# --- TAB-T-Q09: SQLite (no dollar quotes) -----------------------------------------------------------------------------


def test_q09_sqlite_script_runs_statement_by_statement() -> None:
    script = """
    CREATE TABLE t (id INTEGER PRIMARY KEY, note TEXT DEFAULT 'a;b'); -- comment; here
    /* block; comment */
    INSERT INTO t (note) VALUES ('x;y'), ("double;quoted");
    CREATE TRIGGER trg AFTER INSERT ON t BEGIN UPDATE t SET note = note WHERE id = 0; END;
    SELECT $p;
    """
    parts = split_statements(script)
    conn = sqlite3.connect(":memory:")
    try:
        for statement in parts[:2]:
            conn.execute(statement)
        # SQLite's $name parameter is no dollar quote: the statement ends at its semicolon.
        assert parts[-1] == "SELECT $p"
        assert conn.execute(parts[-1], {"p": 7}).fetchone() == (7,)
        assert conn.execute("SELECT note FROM t ORDER BY id").fetchall() == [("x;y",), ("double;quoted",)]
    finally:
        conn.close()
    # A trigger body with semicolons is split, as in Axentra (documented limitation, like CREATE RULE).
    assert any(p.startswith("CREATE TRIGGER") for p in parts)


# --- TAB-T-Q10: never raises; pure; identical to Axentra on everything Axentra handles -------------------------------

_ANY_SQL = st.lists(
    st.sampled_from([*"ab $Ee'\"-/*;\n\r\\1_\u00a0", "$$", "/*", "*/", "--", "E'", "$t$"]), max_size=40
).map("".join) | st.text(max_size=40)


@settings(max_examples=1000, suppress_health_check=[HealthCheck.too_slow])
@given(_ANY_SQL)
def test_q10_never_raises_and_returns_stripped_slices_in_order(sql: str) -> None:
    parts = split_statements(sql)
    assert all(p and p == p.strip() for p in parts)
    # Every statement is a slice of the input, in order.
    position = 0
    for part in parts:
        found = sql.find(part, position)
        assert found >= 0
        position = found + len(part)


# Tokens Axentra's splitter handles exactly as PostgreSQL does: no E strings, no nested comments, no digits or "$" in
# dollar tags, no lone carriage returns, no comment-only block statements (each statement starts with code).
_TOKENS = st.sampled_from(
    [
        "SELECT",
        " ",
        "\n",
        "\r\n",
        "1",
        "x",
        "'a;b'",
        "'it''s'",
        "''",
        '"q;id"',
        '"a""b"',
        "$$ ; $$",
        "$tag$ a;b $tag$",
        "-- c; d\n",
        "/* c; d */",
        "/**/",
        "(",
        ")",
        ",",
        "\\",
    ]
)
_STATEMENT = st.lists(_TOKENS, max_size=8).map(lambda tokens: "SELECT " + " ".join(tokens))
_SCRIPT = st.lists(st.tuples(_STATEMENT, st.sampled_from([";", ";\n", " ;  ", ";;"])), max_size=6).map(
    lambda pairs: "".join(statement + end for statement, end in pairs)
)


@settings(max_examples=1000, suppress_health_check=[HealthCheck.too_slow])
@given(_SCRIPT, st.sampled_from(["", "-- tail\n", "  \n", "SELECT 'open"]))
def test_q10_identical_to_axentra_on_inputs_it_splits_correctly(script: str, tail: str) -> None:
    sql = script + tail
    assert split_statements(sql) == axentra_split_statements(sql)


DIFFERENCES = [
    ("SELECT E'it\\'s; ok'; SELECT 2", ["SELECT E'it\\'s; ok'", "SELECT 2"]),
    ("SELECT 1 /* a /* b */ c; */; SELECT 2", ["SELECT 1 /* a /* b */ c; */", "SELECT 2"]),
    ("SELECT $a1$;$a1$; SELECT 2", ["SELECT $a1$;$a1$", "SELECT 2"]),
    ("SELECT a$b$c; SELECT $b$ ; $b$", ["SELECT a$b$c", "SELECT $b$ ; $b$"]),
    ("SELECT 1 -- note\r; SELECT 2", ["SELECT 1 -- note", "SELECT 2"]),
    ("/* only a comment */; SELECT 2", ["SELECT 2"]),
]


@pytest.mark.parametrize(("sql", "expected"), DIFFERENCES)
def test_documented_differences_from_axentra(sql: str, expected: list[str]) -> None:
    """Tabrion follows PostgreSQL; the Axentra oracle gets each of these wrong (negative probe of the vectors)."""
    assert split_statements(sql) == expected
    assert axentra_split_statements(sql) != expected


def test_no_axentra_sql_file_hits_a_difference() -> None:
    """MIG-AXE evidence: no shipped Axentra SQL holds a construct where the two splitters differ."""
    files = _golden()["files"]
    assert isinstance(files, list)
    for entry in files:
        sql = entry["sql"]
        assert "E'" not in sql.replace("TYPE'", ""), entry["path"]
        assert axentra_split_statements(sql) == split_statements(sql), entry["path"]


def test_thread_safe_and_pure() -> None:
    sql = (FUNCTION + "\n") * 20
    expected = split_statements(sql)
    results: list[list[str]] = []

    def run() -> None:
        for _ in range(20):
            results.append(split_statements(sql))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 80 and all(r == expected for r in results)
