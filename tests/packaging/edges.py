"""The optional dependency edges of Tabrion (AC-3, TABRION QA-18) as data, for the blocked-module tests.

An optional edge is an extra of ``pyproject.toml``: what Tabrion offers without it must import while the extra's
modules are blocked (``sys.modules[name] = None``), and what needs it must refuse with the installation hint.
Each edge has exactly one test (``tests/unit/test_optional_edges.py``, one case per entry of :data:`EDGES`), and the
entries must match the extras of ``pyproject.toml`` (a new extra without an entry fails the meta test).

No side effects on import: the module holds names only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Edge:
    """One optional edge: block ``blocked``, import ``imports``, expect ``refuses`` to raise with ``hint``."""

    blocked: tuple[str, ...]
    """Top-level modules of the extra (``sys.modules[name] = None``)."""
    imports: tuple[str, ...]
    """Modules that import without the extra."""
    refuses: tuple[str, ...] = ()
    """``module`` or ``module:attribute`` that raise ``ImportError`` without the extra."""
    hint: str = ""
    """Text every refusal carries (the installation hint)."""
    probe: tuple[str, str] = ("", "")
    """``(file below src/tabrion, line)`` the mutation probe adds to a copy: the edge's test must then fail."""
    requirements: tuple[str, ...] = field(default=())
    """Distribution names the extra lists in ``pyproject.toml`` (checked by the meta test)."""


CORE = ("tabrion", "tabrion.errors", "tabrion.sql", "tabrion.config", "tabrion.sqlite", "tabrion.check")
POSTGRES = ("tabrion.postgres", "tabrion.postgres.roles", "tabrion.postgres.locks")

EDGES: dict[str, Edge] = {
    # tabrion[postgres] -> psycopg: everything imports; psycopg is loaded when a connection is made.
    "postgres": Edge(
        blocked=("psycopg", "psycopg_binary", "psycopg_c", "psycopg_pool"),
        imports=(*CORE, *POSTGRES),
        probe=("sqlite.py", "import psycopg"),
        requirements=("psycopg",),
    ),
    # tabrion[sqlalchemy] -> sqlalchemy: layer A (and tabrion.postgres) without it; layer B names give the hint.
    "sqlalchemy": Edge(
        blocked=("sqlalchemy",),
        imports=(*CORE, *POSTGRES),
        refuses=("tabrion:Database", "tabrion:Profile", "tabrion:Transaction"),
        hint='pip install "tabrion[sqlalchemy]"',
        probe=("postgres/roles.py", "import sqlalchemy"),
        requirements=("sqlalchemy",),
    ),
    # tabrion[migrations] -> alembic: everything but tabrion.migrations.
    "migrations": Edge(
        blocked=("alembic",),
        imports=(*CORE, *POSTGRES),
        refuses=("tabrion.migrations",),
        hint='pip install "tabrion[migrations]"',
        probe=("check.py", "import alembic"),
        requirements=("alembic", "tabrion"),
    ),
    # tabrion[testing] -> pytest: everything but tabrion.testing.
    "testing": Edge(
        blocked=("pytest", "_pytest"),
        imports=(*CORE, *POSTGRES),
        refuses=("tabrion.testing",),
        hint='pip install "tabrion[testing]"',
        probe=("errors.py", "import pytest"),
        requirements=("pytest",),
    ),
}
"""Extra name -> edge. ``all`` and ``dev`` bundle the others and are no edge of their own."""

BUNDLES = frozenset({"all", "dev", "docs"})
"""Extras that only combine other extras or tools (no edge)."""


def check_source(edge: Edge) -> str:
    """Source for ``python -I -c``: block the extra, import what must import, check every refusal and its hint."""
    return (
        "import importlib, sys\n"
        f"for _n in {edge.blocked!r}:\n"
        "    sys.modules[_n] = None\n"
        f"for _m in {edge.imports!r}:\n"
        "    importlib.import_module(_m)\n"
        f"for _r in {edge.refuses!r}:\n"
        "    _mod, _, _attr = _r.partition(':')\n"
        "    try:\n"
        "        _o = importlib.import_module(_mod)\n"
        "        if _attr:\n"
        "            getattr(_o, _attr)\n"
        "    except ImportError as _e:\n"
        f"        assert {edge.hint!r} in str(_e), (_r, str(_e))\n"
        "    else:\n"
        "        raise AssertionError(_r + ' imported without the extra')\n"
        f"_loaded = sorted(n for n in sys.modules if n.partition('.')[0] in {edge.blocked!r} and sys.modules[n])\n"
        "assert not _loaded, _loaded\n"
        "print('edge ok')\n"
    )
