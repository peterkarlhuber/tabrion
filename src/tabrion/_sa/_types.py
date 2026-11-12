"""Documentation-only type aliases of layer B. Private: not exported from ``tabrion`` (G-T1 F-11, TODO TB-020).

They live in a module of their own, so ``tabrion._sa.database`` and the staged 0.2 methods in ``tabrion._sa._v02``
can both import them without importing each other (G-T1 condition check CC-2, TODO TB-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias

import sqlalchemy

__all__: list[str] = []

Statement: TypeAlias = str | sqlalchemy.sql.Executable
"""A ``str`` becomes ``sqlalchemy.text()`` with named parameters (``:name``)."""
Params: TypeAlias = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None
"""A sequence of mappings runs ``executemany``."""
Isolation: TypeAlias = Literal["read_committed", "repeatable_read", "serializable"]
