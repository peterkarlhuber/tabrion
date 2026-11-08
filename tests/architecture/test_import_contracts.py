"""QA-16 (AC-1): the import-linter contracts follow the edge list K-33, and a forbidden import turns them red.

* ``lint-imports`` on the repository passes (positive control).
* Mutation probe (07 section 7, ``test_forbidden_probe``): a copy of the package with one forbidden import fails
  ``lint-imports`` and names the broken contract.
* The contracts name every module of the package, so a new module cannot slip past them.
* An AST scan catches imports of any ``nodestro*`` package, a prefix import-linter cannot express.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

from tests.conftest import ROOT, SRC

PYPROJECT = ROOT / "pyproject.toml"
LEVUVO_PREFIXES = ("orkexa", "axentra", "nodestro", "levuvo", "tabrion_studio")


def _lint_imports(cwd: Path, src: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(src)
    exe = Path(sys.executable).with_name("lint-imports")
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter.cli"]
    return subprocess.run(
        [*cmd, "--config", str(cwd / "pyproject.toml"), "--no-cache"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _copy(tmp_path: Path) -> Path:
    shutil.copytree(SRC / "tabrion", tmp_path / "src" / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PYPROJECT, tmp_path / "pyproject.toml")
    return tmp_path


def _inject(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    assert anchor in text, path
    path.write_text(text.replace(anchor, anchor + "\n" + line + "\n", 1), encoding="utf-8")


def test_contracts_are_kept() -> None:
    result = _lint_imports(ROOT, SRC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_unmodified_copy_is_kept(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    result = _lint_imports(root, root / "src")
    assert result.returncode == 0, result.stdout + result.stderr


NO_LEVUVO = "Tabrion depends on no Levuvo platform"
CORE = "Driver core is stdlib-only"
POSTGRES = "PostgreSQL helpers need only psycopg"
LAYERS = "Layer B builds on layer A, never the reverse"


@pytest.mark.parametrize(
    ("module", "line", "contract"),
    [
        ("sql.py", "import orkexa", NO_LEVUVO),
        ("_sa/database.py", "import axentra", NO_LEVUVO),
        ("postgres/roles.py", "import nodestro_runtime", NO_LEVUVO),
        ("check.py", "import nodestro_api", NO_LEVUVO),
        ("testing/__init__.py", "import tabrion_studio", NO_LEVUVO),
        ("config.py", "import axentra_studio", NO_LEVUVO),
        ("sqlite.py", "import psycopg", CORE),
        ("errors.py", "import sqlalchemy", CORE),
        ("check.py", "import tabrion.postgres", CORE),
        ("sql.py", "import alembic", CORE),
        ("postgres/__init__.py", "import sqlalchemy", POSTGRES),
        ("postgres/locks.py", "import psycopg_pool", POSTGRES),
        ("sqlite.py", "import tabrion._sa.database", LAYERS),
    ],
)
def test_mutation_probe_turns_red(tmp_path: Path, module: str, line: str, contract: str) -> None:
    root = _copy(tmp_path)
    _inject(root / "src" / "tabrion" / module, line)
    result = _lint_imports(root, root / "src")
    assert result.returncode != 0, f"{line!r} in {module} went unnoticed:\n{result.stdout}"
    assert f"{contract}" in result.stdout
    broken = [ln for ln in result.stdout.splitlines() if ln.startswith(contract)]
    assert broken, result.stdout
    assert broken[0].rstrip().endswith("BROKEN"), result.stdout


def _contracts() -> list[dict[str, object]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    contracts: list[dict[str, object]] = data["tool"]["importlinter"]["contracts"]
    return contracts


def _top_level_modules() -> set[str]:
    names = set()
    for path in (SRC / "tabrion").iterdir():
        if path.name.startswith("__") or path.name == "py.typed":
            continue
        if path.is_dir() and (path / "__init__.py").exists():
            names.add(f"tabrion.{path.name}")
        elif path.suffix == ".py":
            names.add(f"tabrion.{path.stem}")
    return names


def test_layers_name_every_module() -> None:
    """Every module except ``_version`` sits in a layer, so the layer rule covers new modules too."""
    (layers,) = [c for c in _contracts() if c["type"] == "layers"]
    in_layers = {m.strip() for layer in layers["layers"] for m in str(layer).split("|")}  # type: ignore[attr-defined]
    assert _top_level_modules() - {"tabrion._version"} == in_layers


def test_core_contract_names_the_stdlib_modules() -> None:
    (core,) = [c for c in _contracts() if c["name"] == CORE]
    assert set(core["source_modules"]) == {  # type: ignore[call-overload]
        "tabrion.errors",
        "tabrion.sql",
        "tabrion.config",
        "tabrion.sqlite",
        "tabrion.check",
    }
    assert {"psycopg", "sqlalchemy", "alembic", "pytest"} <= set(core["forbidden_modules"])  # type: ignore[call-overload]


def test_levuvo_contract_names_every_known_platform() -> None:
    (contract,) = [c for c in _contracts() if c["name"].startswith(NO_LEVUVO)]  # type: ignore[attr-defined]
    forbidden = set(contract["forbidden_modules"])  # type: ignore[call-overload]
    assert contract["source_modules"] == ["tabrion"]
    assert {
        "orkexa",
        "axentra",
        "axentra_studio",
        "nodestro",
        "nodestro_runtime",
        "nodestro_api",
        "nodestro_studio",
        "nodestro_examples",
        "nodestro_integrations",
        "nodestro_sdk",
        "tabrion_studio",
    } <= forbidden


def _imported_top_levels(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.partition(".")[0])
    return names


def test_no_levuvo_import_by_prefix() -> None:
    """Covers ``nodestro*`` and any future Levuvo package, also in ``TYPE_CHECKING`` blocks."""
    offenders = []
    for path in sorted((SRC / "tabrion").rglob("*.py")):
        for name in _imported_top_levels(path):
            if name.startswith(LEVUVO_PREFIXES):
                offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert not offenders


def test_prefix_scan_detects_a_probe(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("from nodestro_server import x\n", encoding="utf-8")
    assert _imported_top_levels(probe) == {"nodestro_server"}
