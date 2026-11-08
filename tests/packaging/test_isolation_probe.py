"""TAB-005 negative probe: a deliberate psycopg import in ``tabrion.sqlite`` turns the isolation check red.

The probe copies the package, adds ``import psycopg`` to the copy of ``tabrion/sqlite.py`` and runs the same check
as ``test_import_isolation`` against the copy. The unmodified copy is the positive control.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.conftest import SRC
from tests.packaging.blocking import BLOCK_SOURCE
from tests.support import isolation_check_source, run_python


def _copy_package(tmp_path: Path) -> Path:
    target = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", target / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _inject(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    assert anchor in text
    path.write_text(text.replace(anchor, anchor + "\n" + line + "\n", 1), encoding="utf-8")


def test_unmodified_copy_passes(tmp_path: Path) -> None:
    src = _copy_package(tmp_path)
    result = run_python(BLOCK_SOURCE + isolation_check_source(), path=src)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("module", "line"),
    [
        ("sqlite.py", "import psycopg"),
        ("sqlite.py", "import sqlalchemy"),
        ("errors.py", "import psycopg"),
        ("check.py", "import alembic"),
    ],
)
def test_forbidden_import_in_the_core_fails_with_blocked_extras(tmp_path: Path, module: str, line: str) -> None:
    src = _copy_package(tmp_path)
    _inject(src / "tabrion" / module, line)
    result = run_python(BLOCK_SOURCE + isolation_check_source(), path=src)
    assert result.returncode != 0, "the probe import went unnoticed"
    assert "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr
