"""PostgreSQL checks of ``tabrion.check.check`` (TAB-PG-*; private, layer A, extra ``postgres``).

``tabrion.check`` loads this module by name when the target is a ``psycopg.Connection`` (G-T1 condition C-1, fix
B; contract section 7). It may import ``tabrion.check`` and the rest of ``tabrion.postgres``, never layer B. Stub;
implemented in TAB-017.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import psycopg

    from tabrion.check import Finding

__all__ = ["collect"]


def collect(
    target: psycopg.Connection[Any], *, expect: Literal["runtime", "owner", "any"] = "runtime"
) -> list[Finding]:
    """The TAB-PG-* findings for a psycopg connection, read only (uses ``diagnose`` and ``role_info``)."""
    raise NotImplementedError("tabrion.postgres._check.collect: implemented in TAB-017")
