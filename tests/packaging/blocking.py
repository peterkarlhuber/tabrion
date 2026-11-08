"""The names of the optional dependencies and a blockade as source text (no side effects on import)."""

from __future__ import annotations

BLOCKED = ("psycopg", "psycopg_binary", "psycopg_c", "psycopg_pool", "sqlalchemy", "alembic")
"""The modules of the extras postgres, sqlalchemy and migrations."""

BLOCK_SOURCE = f"import sys\nfor _n in {BLOCKED!r}:\n    sys.modules[_n] = None\n"
"""``sys.modules[name] = None`` makes ``import name`` raise ``ImportError``; for ``python -I -c`` subprocesses."""
