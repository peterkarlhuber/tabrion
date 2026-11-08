"""Layer-B checks of ``tabrion.check.check`` for a ``tabrion.Database`` (private, extra ``sqlalchemy``).

``tabrion.check`` loads this module by name when the target is a ``Database`` (G-T1 condition C-1, fix B; contract
section 7). It runs the SQLite checks of ``tabrion.check`` or the PostgreSQL checks of ``tabrion.postgres._check`` on
a raw driver connection and adds the layer-B findings (pool, profile). Still a stub: it follows the layer-B core
(TAB-018) as TODO TB-060 (contract I-55).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tabrion._sa.database import Database
    from tabrion.check import Finding

__all__ = ["collect"]


def collect(target: Database, *, expect: Literal["runtime", "owner", "any"] = "runtime") -> list[Finding]:
    """The findings for a ``Database``: those of its dialect plus the layer-B ones, read only."""
    raise NotImplementedError("tabrion._sa._check.collect: follows the layer-B core (TODO TB-060)")
