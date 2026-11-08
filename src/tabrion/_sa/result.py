"""Materialized query results for tools such as Tabrion Studio (since 0.2). Private: use ``tabrion.Result``.

Contract: ``docs/api/contract.md`` section 5.4; implemented in TAB-033.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["Column", "Result"]


@dataclass(frozen=True, slots=True)
class Column:
    """Since 0.2."""

    name: str
    type_name: str | None
    """PostgreSQL: type name by OID lookup; SQLite: the declared type or ``None``."""
    nullable: bool | None


@dataclass(frozen=True, slots=True)
class Result:
    """Since 0.2. Immutable and fully materialized (up to ``max_rows``)."""

    columns: tuple[Column, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    """Rows delivered."""
    truncated: bool
    """More rows existed than ``max_rows``."""
    elapsed_ms: float
    statement_kind: Literal["select", "dml", "ddl", "other"]
