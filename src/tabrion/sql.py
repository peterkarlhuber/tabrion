"""SQL text helpers (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 2.1. Stub for the API freeze G-T1; the implementation follows in TAB-011.
"""

from __future__ import annotations

__all__ = ["split_statements"]


def split_statements(sql: str) -> list[str]:
    """Split SQL at top-level semicolons (PostgreSQL quoting rules, nested block comments).

    Empty and comment-only statements are dropped, comments inside a statement are kept and every result is
    stripped. Never raises: an unterminated quote or comment yields the rest as the last statement. The output is
    byte-identical to Axentra's splitter for every input Axentra splits correctly (TAB-T-Q01..Q10).
    """
    raise NotImplementedError("tabrion.sql.split_statements: implemented in TAB-011")
