"""Tabrion: small, secure database foundations for SQLite and PostgreSQL.

Layer A (driver core) lives in ``tabrion.errors``, ``tabrion.sql``, ``tabrion.config``, ``tabrion.sqlite``,
``tabrion.check`` (standard library only) and ``tabrion.postgres`` (extra ``postgres``). Layer B (extra
``sqlalchemy``) is exported here lazily (PEP 562): ``import tabrion`` always works, and a layer-B name without
SQLAlchemy raises ``ImportError`` with the installation hint.

Since 0.2 (TAB-042) the Studio APIs are public: ``Result``, ``Column`` and the 0.2 methods of ``Database``
(``query``, ``inspector``, experimental ``on_begin`` and ``run_transaction``) and ``Transaction`` (``query``,
``stream``, ``cancel``, experimental ``savepoint``); ``docs/api/surface.json`` freezes them.
``scripts/api_surface.py --no-stubs`` fails if a name of the package's minor is a stub (TB-022).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tabrion._version import __version__

if TYPE_CHECKING:
    from tabrion._sa.database import Database, Health, Transaction
    from tabrion._sa.profile import Profile, TLSMode
    from tabrion._sa.result import Column, Result
    from tabrion.config import safe_url
    from tabrion.errors import TabrionError

__all__ = [
    "Column",
    "Database",
    "Health",
    "Profile",
    "Result",
    "TLSMode",
    "TabrionError",
    "Transaction",
    "__version__",
    "safe_url",
]

# Layer-A names: standard library only, always importable.
_LAYER_A = {"safe_url": "tabrion.config", "TabrionError": "tabrion.errors"}
# Layer-B names: need the extra "sqlalchemy".
_LAYER_B = {
    "Database": "tabrion._sa.database",
    "Health": "tabrion._sa.database",
    "Transaction": "tabrion._sa.database",
    "Profile": "tabrion._sa.profile",
    "TLSMode": "tabrion._sa.profile",
    "Result": "tabrion._sa.result",
    "Column": "tabrion._sa.result",
}

#: Never the bare requirement ``tabrion[...]`` without its ``@ git+...`` part: Tabrion is not on PyPI, so that would
#: ask a package index for a package that is not ours (dependency confusion, TB-132, DC-TB-1).
_SQLALCHEMY_HINT = (
    'tabrion.{name} needs the extra "sqlalchemy": '
    'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
    " (Tabrion is not on PyPI: pin the commit you use)"
)


def __getattr__(name: str) -> Any:
    if name in _LAYER_A:
        return getattr(importlib.import_module(_LAYER_A[name]), name)
    if name in _LAYER_B:
        try:
            module = importlib.import_module(_LAYER_B[name])
        except ModuleNotFoundError as exc:
            if exc.name is not None and exc.name.partition(".")[0] == "sqlalchemy":
                raise ImportError(_SQLALCHEMY_HINT.format(name=name), name="sqlalchemy") from None
            raise
        return getattr(module, name)
    raise AttributeError(f"module 'tabrion' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
