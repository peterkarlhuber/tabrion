"""AC-3 (TABRION QA-18), document 05 section 3.2: Tabrion imports with every Levuvo platform blocked.

The forbidden edges of K-33 (document 13, section 2.2): Tabrion never imports orkexa, axentra, nodestro_* or
tabrion_studio. A meta path finder in a fresh ``python -I`` refuses those names (even where they are installed), and
every module of the package is imported. The probe adds such an import to a copy of the package; the check must then
fail. Not marked ``packaging``: it runs in the default test job (and needs the dev extras, as every module is imported).
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.conftest import SRC
from tests.support import run_python

FORBIDDEN = ("orkexa", "axentra", "tabrion_studio", "levuvo")
FORBIDDEN_PREFIXES = ("nodestro",)


def _modules(src: Path) -> list[str]:
    root = src / "tabrion"
    names = []
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return names


def _source(modules: list[str]) -> str:
    return (
        "import importlib, importlib.abc, sys\n"
        f"FORBIDDEN, PREFIXES = {FORBIDDEN!r}, {FORBIDDEN_PREFIXES!r}\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        top = name.partition('.')[0]\n"
        "        if top in FORBIDDEN or top.startswith(PREFIXES):\n"
        "            raise ImportError(f'forbidden edge: tabrion imports {name}', name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"for m in {modules!r}:\n"
        "    importlib.import_module(m)\n"
        "loaded = sorted(n for n in sys.modules if n.partition('.')[0] in FORBIDDEN or n.startswith(PREFIXES))\n"
        "assert not loaded, loaded\n"
        "print(len(" + repr(modules) + "), 'modules independent')\n"
    )


def test_every_module_imports_with_the_platforms_blocked() -> None:
    modules = _modules(SRC)
    assert "tabrion.postgres.roles" in modules and "tabrion._sa.database" in modules
    result = run_python(_source(modules))
    assert result.returncode == 0, result.stderr
    assert f"{len(modules)} modules independent" in result.stdout


@pytest.mark.parametrize(
    "line",
    ["import orkexa", "import axentra.data", "from nodestro_runtime import workspace", "import tabrion_studio"],
)
def test_a_platform_import_turns_the_check_red(tmp_path: Path, line: str) -> None:
    """Mutation probe: one forbidden import in a copy of ``tabrion/sql.py`` fails the check."""
    src = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", src / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    path = src / "tabrion" / "sql.py"
    anchor = "from __future__ import annotations\n"
    path.write_text(path.read_text("utf-8").replace(anchor, anchor + "\n" + line + "\n", 1), "utf-8")
    result = run_python(_source(_modules(src)), path=src)
    assert result.returncode != 0, f"{line} went unnoticed"
    assert "forbidden edge" in result.stderr
