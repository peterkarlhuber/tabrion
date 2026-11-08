"""TAB-023 (G-T1 condition C-6): ``scripts/api_surface.py`` freezes every signature and keeps 0.2 names out of 0.1.

The positive control runs the check on the repository; the mutation probes change one thing in a copy of the package
and expect the check to fail and name it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.conftest import ROOT, SRC

for _extra in ("sqlalchemy", "alembic"):
    pytest.importorskip(_extra)

SCRIPT = ROOT / "scripts" / "api_surface.py"
SURFACE = ROOT / "docs" / "api" / "surface.json"


def _run(src: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(src)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--surface", str(SURFACE), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


Edit = tuple[str, str, str]
"""(module path below src/tabrion, old text, new text)."""


def _mutated(tmp_path: Path, edits: list[Edit]) -> Path:
    src = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", src / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    for module, old, new in edits:
        path = src / "tabrion" / module
        text = path.read_text(encoding="utf-8")
        assert old in text, (module, old)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return src


def test_surface_is_consistent() -> None:
    result = _run(SRC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "consistent" in result.stdout


MUTATIONS: dict[str, tuple[list[Edit], str]] = {
    "changed default": (
        [("_sa/database.py", "timeout: float = 5.0, deep", "timeout: float = 6.0, deep")],
        "tabrion.Database.health",
    ),
    "keyword-only becomes positional": (
        [
            (
                "sqlite.py",
                "path: str | os.PathLike[str],\n    *,\n    profile",
                "path: str | os.PathLike[str],\n    profile",
            )
        ],
        "tabrion.sqlite.connect",
    ),
    "dataclass field default": (
        [("postgres/__init__.py", "statement_timeout_ms: int | None = 30_000", "statement_timeout_ms: int | None = 1")],
        "tabrion.postgres.SessionSettings",
    ),
    "0.2 name exported in 0.1": (
        [
            ("__init__.py", '    "Database",\n', '    "Database",\n    "Result",\n'),
            ("__init__.py", "_LAYER_B = {\n", '_LAYER_B = {\n    "Result": "tabrion._sa.result",\n'),
        ],
        "tabrion.Result: since 0.2",
    ),
    "0.2 name importable through __getattr__ only": (
        [("__init__.py", "_LAYER_B = {\n", '_LAYER_B = {\n    "Result": "tabrion._sa.result",\n')],
        "tabrion.Result: since 0.2, but it is importable from its public path",
    ),
    "0.2 mixin mixed in early": (
        [
            ("_sa/database.py", "class Database:", "class Database(_DatabaseV02):"),
            (
                "_sa/database.py",
                "\n\n\nclass Database(",
                "\n\nfrom tabrion._sa._v02 import _DatabaseV02  # noqa: E402\n\n\nclass Database(",
            ),
        ],
        "tabrion.Database.query: since 0.2",
    ),
    "staged 0.2 signature changed": (
        [("_sa/_v02.py", "batch_size: int = 500", "batch_size: int = 100")],
        "tabrion.Transaction.stream: staged signature changed",
    ),
    "new public name": (
        [
            (
                "sql.py",
                '__all__ = ["split_statements"]',
                '__all__ = ["split_statements", "extra"]\n\n\ndef extra() -> None: ...',
            )
        ],
        "tabrion.sql.extra: new public name",
    ),
}


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_mutation_turns_red(tmp_path: Path, case: str) -> None:
    edits, message = MUTATIONS[case]
    result = _run(_mutated(tmp_path, edits))
    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr, result.stderr
