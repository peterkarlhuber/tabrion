"""Axentra's statement splitter, verbatim, as the test oracle of TAB-011 (TAB-T-Q01, Q02).

Copied from peterkarlhuber/axentra ``src/axentra/data/migrations.py`` (``split_statements``, ``_has_code`` and
``_DOLLAR_TAG``) at commit 9c4666f (MIT, Copyright (c) 2026 Peter Karlhuber). Only the names changed; the logic must
stay byte for byte as it is, because ``tabrion.sql.split_statements`` is checked against it.
``axentra_splitter.json`` holds its output for every SQL file Axentra ships at that commit;
``tests/unit/test_sql.py`` checks that this copy still reproduces it, so an edit here cannot go unnoticed.
"""

from __future__ import annotations

import re

AXENTRA_COMMIT = "9c4666f"

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def axentra_split_statements(sql: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end < 0 else end
            buf.append(sql[i:end])
            i = end
        elif c == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            buf.append(sql[i:end])
            i = end
        elif c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # doubled quote
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i : j + 1])
            i = j + 1
        elif c == "$" and (m := _DOLLAR_TAG.match(sql, i)):
            tag = m.group(0)
            end = sql.find(tag, i + len(tag))
            end = n if end < 0 else end + len(tag)
            buf.append(sql[i:end])
            i = end
        elif c == ";":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if _has_code(s)]


def _has_code(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False
