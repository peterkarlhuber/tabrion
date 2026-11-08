"""SQL text helpers (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 2.1. :func:`split_statements` replaces Axentra's splitter
(``axentra.data.migrations.split_statements``) and gives the same output for every input that one splits correctly
(TAB-T-Q01, Q02). It differs only where Axentra splits wrongly by PostgreSQL's lexical rules:

* ``E'...'`` strings with backslash escapes (``E'it\\'s'``);
* nested block comments (``/* /* */ */``);
* dollar-quote tags with digits (``$a1$``), and a ``$`` that continues an identifier (``a$b$``), which is no tag;
* a ``--`` comment ends at a carriage return as well as at a line feed;
* a statement that holds nothing but block comments is dropped like one of ``--`` comments.

Not handled, as in Axentra: the parentheses of ``CREATE RULE ... DO (...; ...)`` and ``BEGIN ATOMIC ... END``
function bodies (PostgreSQL 14). Write such statements with a dollar-quoted body or run them on their own.
"""

from __future__ import annotations

__all__ = ["split_statements"]


def _ident_start(c: str) -> bool:
    """PostgreSQL ``ident_start``: a letter, ``_`` or any non-ASCII character."""
    return (c.isascii() and (c.isalpha() or c == "_")) or not c.isascii()


def _ident_cont(c: str) -> bool:
    """PostgreSQL ``ident_cont``: ``ident_start``, a digit or ``$``."""
    return _ident_start(c) or c in "0123456789$"


def _dollar_tag(sql: str, i: int) -> str | None:
    """The dollar-quote delimiter starting at ``sql[i] == "$"`` (``$$`` or ``$tag$``), or ``None``."""
    j = i + 1
    n = len(sql)
    if j < n and _ident_start(sql[j]):
        j += 1
        while j < n and _ident_cont(sql[j]) and sql[j] != "$":
            j += 1
    if j < n and sql[j] == "$":
        return sql[i : j + 1]
    return None


def split_statements(sql: str) -> list[str]:
    """Split SQL at top-level semicolons (PostgreSQL quoting rules, nested block comments).

    Empty and comment-only statements are dropped, comments inside a statement are kept and every result is
    stripped. Never raises: an unterminated quote or comment yields the rest as the last statement. The output is
    byte-identical to Axentra's splitter for every input Axentra splits correctly (TAB-T-Q01..Q10).
    """
    out: list[str] = []
    n = len(sql)
    start = 0  # start of the current statement
    code = False  # the current statement holds something besides white space and comments
    i = 0
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif c == "-" and sql.startswith("--", i):
            while i < n and sql[i] not in "\n\r":
                i += 1
        elif c == "/" and sql.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                code = True  # unterminated: the server reports it, so the statement is kept
        elif c == "'" or c == '"':
            code = True
            # E'...' (the E is a word of its own) takes backslash escapes; '...' and "..." double their quote.
            escapes = c == "'" and i > 0 and sql[i - 1] in "Ee" and (i < 2 or not _ident_cont(sql[i - 2]))
            i += 1
            while i < n:
                if escapes and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == c:
                    if i + 1 < n and sql[i + 1] == c:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif c == "$" and (tag := _dollar_tag(sql, i)) is not None:
            code = True
            end = sql.find(tag, i + len(tag))
            i = n if end < 0 else end + len(tag)
        elif _ident_start(c):
            # A whole word, so that a "$" inside it (a$b$) does not start a dollar quote.
            code = True
            i += 1
            while i < n and _ident_cont(sql[i]):
                i += 1
        elif c == ";":
            if code:
                out.append(sql[start:i].strip())
            start, code = i + 1, False
            i += 1
        else:
            code = True
            i += 1
    if code:
        out.append(sql[start:].strip())
    return [statement for statement in out if statement]
