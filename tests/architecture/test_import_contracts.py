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
    # The console script's entry point, called through the running interpreter: ``python -m importlinter.cli`` does
    # nothing (the module has no ``__main__`` block), and the script's location differs between POSIX and Windows.
    entry = "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"
    cmd = [sys.executable, "-c", entry]
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
    assert "0 broken" in result.stdout, "lint-imports produced no report"


NO_LEVUVO = "Tabrion depends on no Levuvo platform"
CORE = "Driver core is stdlib-only"
POSTGRES = "PostgreSQL helpers need only psycopg"
LAYERS = "Layer B builds on layer A, never the reverse"
LAYER_B = "Layer B needs only SQLAlchemy"
MIGRATIONS = "Migration helpers never import pytest"


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
        ("errors.py", "import tabrion.config", LAYERS),
        ("errors.py", "import tabrion.sql", LAYERS),
        ("sql.py", "import tabrion.config", LAYERS),
        ("_sa/profile.py", "import pytest", LAYER_B),
        ("_sa/database.py", "import alembic", LAYER_B),
        ("_sa/_check.py", "import psycopg_pool", LAYER_B),
        ("migrations.py", "import pytest", MIGRATIONS),
        ("migrations.py", "import tabrion.testing.profiles", MIGRATIONS),
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
        "tabrion._deprecated",
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


def test_layer_b_and_migrations_contracts() -> None:
    """G-T1 condition C-5 (doc 02 section 2): layer B and the migration helpers have their own denylists."""
    by_name = {c["name"]: c for c in _contracts()}
    assert by_name[LAYER_B]["source_modules"] == ["tabrion._sa"]
    assert {"pytest", "alembic", "psycopg_pool", "tabrion.testing", "tabrion.migrations"} <= set(
        by_name[LAYER_B]["forbidden_modules"]  # type: ignore[call-overload]
    )
    assert by_name[MIGRATIONS]["source_modules"] == ["tabrion.migrations"]
    assert {"pytest", "tabrion.testing"} <= set(by_name[MIGRATIONS]["forbidden_modules"])  # type: ignore[call-overload]


def test_no_contract_ignores_imports() -> None:
    """C-1 fix B needs no exception: an ``ignore_imports`` entry would hide a real edge from every contract."""
    assert not [c["name"] for c in _contracts() if c.get("ignore_imports")]


CHECK_COLLECTORS = {"psycopg": "tabrion.postgres._check", "tabrion._sa.database": "tabrion._sa._check"}


def test_check_imports_only_the_standard_library() -> None:
    """C-1 fix B: ``tabrion.check`` has no import of ``tabrion.postgres`` or layer B, at any level of nesting.

    Imports under ``if TYPE_CHECKING:`` are allowed (type checkers only). The two collectors are reached through
    the fixed table ``_COLLECTORS`` and ``importlib.import_module``; the table may name nothing else.
    """
    path = SRC / "tabrion" / "check.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    type_checking: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            type_checking.update(id(child) for stmt in node.body for child in ast.walk(stmt))
    offenders = []
    for node in ast.walk(tree):
        if id(node) in type_checking:
            continue
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] if node.level == 0 else ["." + (node.module or "")]
        else:
            continue
        offenders += [n for n in names if n.partition(".")[0] not in sys.stdlib_module_names]
    assert not offenders, f"tabrion.check imports {offenders}"
    import tabrion.check

    assert tabrion.check._COLLECTORS == CHECK_COLLECTORS
    for collector in CHECK_COLLECTORS.values():
        assert (SRC / Path(*collector.split("."))).with_suffix(".py").is_file(), collector


def test_check_dispatches_by_the_type_module() -> None:
    import sqlite3

    import tabrion.check

    conn = sqlite3.connect(":memory:")
    try:
        assert tabrion.check._collector(conn) is None
    finally:
        conn.close()
    with pytest.raises(TypeError, match=r"expects a sqlite3\.Connection"):
        tabrion.check._collector(object())
    # tabrion.postgres needs psycopg only for type checking (G-7), so this runs without the extra too.
    fake_psycopg = type("Connection", (), {"__module__": "psycopg.connection"})
    collector = tabrion.check._collector(fake_psycopg())
    assert collector is not None
    assert collector.__name__ == "tabrion.postgres._check"


def test_check_dispatches_a_database_to_layer_b() -> None:
    pytest.importorskip("sqlalchemy")
    from tabrion._sa.database import Database
    import tabrion.check

    subclass = type("AppDatabase", (Database,), {"__module__": "app.db"})
    # Database() raises TypeError (G-T1 F-14); the dispatch looks only at the type.
    collector = tabrion.check._collector(object.__new__(subclass))
    assert collector is not None
    assert collector.__name__ == "tabrion._sa._check"


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


# G-T1 F-09 and F-10 (TODO TB-019): "stdlib-only" as an allowlist. import-linter's core contract forbids a list of
# packages, so any other third-party import (for example ``pluggy``, which comes with pytest) would pass it, and the
# root package ``tabrion`` (whose ``__getattr__`` reaches layer B) is in no contract at all.
CORE_FILES = ("errors.py", "sql.py", "config.py", "sqlite.py", "check.py", "_deprecated.py")
CORE_IMPORTABLE = frozenset(
    {"tabrion.errors", "tabrion.sql", "tabrion.config", "tabrion.sqlite", "tabrion.check", "tabrion._deprecated"}
)


def _core_import_offenders(source: str, filename: str = "<probe>") -> list[str]:
    """Imports outside ``if TYPE_CHECKING:`` that are neither standard library nor a layer-A core module."""
    tree = ast.parse(source, filename=filename)
    type_checking: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            type_checking.update(id(child) for stmt in node.body for child in ast.walk(stmt))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if id(node) in type_checking:
            continue
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                offenders.append("." * node.level + (node.module or ""))
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.partition(".")[0]
            if root == "tabrion":
                if name not in CORE_IMPORTABLE:  # also "import tabrion" and "from tabrion import X"
                    offenders.append(name)
            elif root not in sys.stdlib_module_names:
                offenders.append(name)
    return offenders


@pytest.mark.parametrize("filename", CORE_FILES)
def test_core_imports_only_the_standard_library_and_core_modules(filename: str) -> None:
    path = SRC / "tabrion" / filename
    assert not _core_import_offenders(path.read_text(encoding="utf-8"), str(path)), filename


@pytest.mark.parametrize(
    ("line", "offender"),
    [
        ("import pluggy", "pluggy"),
        ("from tabrion import Database", "tabrion"),
        ("import tabrion", "tabrion"),
        ("from tabrion._sa import database", "tabrion._sa"),
        ("from . import postgres", "."),
        ("def f():\n    import yaml", "yaml"),
    ],
)
def test_core_allowlist_probe(line: str, offender: str) -> None:
    """Negative probe: each forbidden import is reported; the same line under ``TYPE_CHECKING`` is not."""
    assert _core_import_offenders(f"import os\n{line}\n") == [offender]
    guarded = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n" + "\n".join(
        "    " + ln for ln in line.splitlines()
    )
    assert _core_import_offenders(guarded) == []
