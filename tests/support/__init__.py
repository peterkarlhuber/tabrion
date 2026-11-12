"""Helpers for tests that run Python in a fresh interpreter (no third-party imports)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

CORE_MODULES = (
    "tabrion",
    "tabrion.errors",
    "tabrion.sql",
    "tabrion.config",
    "tabrion.sqlite",
    "tabrion.check",
    "tabrion._deprecated",
)
"""Layer A without extras (05 section 2.3); importing them loads no optional dependency."""

OPTIONAL = ("psycopg", "psycopg_pool", "sqlalchemy", "alembic", "pytest")


def run_python(code: str, *, path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``code`` with ``python -I`` (no user site, no PYTHONPATH, no cwd on sys.path).

    ``path`` is put first on ``sys.path`` (a copy of the package for mutation probes).
    """
    if path is not None:
        code = f"import sys\nsys.path.insert(0, {str(path)!r})\n" + code
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    return subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, timeout=120, check=False
    )


def isolation_check_source() -> str:
    """Source that imports the core modules and fails when an optional dependency got loaded (TAB-T-182)."""
    return (
        "import importlib, sys\n"
        f"for name in {CORE_MODULES!r}:\n"
        "    importlib.import_module(name)\n"
        f"optional = {OPTIONAL!r}\n"
        "loaded = sorted(n for n in sys.modules if n.partition('.')[0] in optional and sys.modules[n] is not None)\n"
        "assert not loaded, f'core imports loaded optional modules: {loaded}'\n"
        "print('core isolated')\n"
    )
