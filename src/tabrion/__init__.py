"""Tabrion: small, secure database foundations for SQLite and PostgreSQL.

Layer A (driver core) lives in ``tabrion.errors``, ``tabrion.sql``, ``tabrion.config``, ``tabrion.sqlite``,
``tabrion.check`` (standard library only) and ``tabrion.postgres`` (extra ``postgres``). Layer B (extra
``sqlalchemy``) is exported here lazily (PEP 562): ``import tabrion`` always works, and a layer-B name without
SQLAlchemy raises ``ImportError`` with the installation hint.

Status: API stubs for the API freeze G-T1 (milestone M1). Calling any function raises ``NotImplementedError``.
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
    "Column": "tabrion._sa.result",
    "Result": "tabrion._sa.result",
}

_SQLALCHEMY_HINT = 'tabrion.{name} needs the extra "sqlalchemy": pip install "tabrion[sqlalchemy]"'


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
