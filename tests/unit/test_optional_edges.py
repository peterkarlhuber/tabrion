"""AC-3 (TABRION QA-18): one blocked-module test per optional edge of Tabrion, with a mutation probe each.

Each extra of ``pyproject.toml`` is an optional edge (``tests/packaging/edges.py``). Its test runs a fresh
``python -I`` with the extra's modules blocked (``sys.modules[name] = None``): what Tabrion offers without the extra
imports, what needs it refuses with the installation hint. The probe adds a top-level import of the extra to a copy of
the package; the same check must then fail, so the test cannot pass by accident. The meta test keeps the edge list
equal to the extras, so a new extra cannot go without its test. The forbidden edges (Tabrion never imports orkexa,
axentra, nodestro_* or tabrion_studio) are ``tests/packaging/test_independent.py``.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import tomllib

import pytest

from tests.conftest import ROOT, SRC
from tests.packaging.edges import BUNDLES, EDGES, check_source
from tests.support import run_python


@pytest.mark.parametrize("extra", sorted(EDGES))
def test_the_edge_imports_without_its_extra(extra: str) -> None:
    result = run_python(check_source(EDGES[extra]))
    assert result.returncode == 0, result.stderr
    assert "edge ok" in result.stdout


def test_every_extra_is_an_edge_with_exactly_one_test() -> None:
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["optional-dependencies"]
    assert set(EDGES) == set(extras) - BUNDLES
    for extra, edge in EDGES.items():
        names = {re.split(r"[\[<>=~!; ]", req, maxsplit=1)[0].lower() for req in extras[extra]}
        assert names == set(edge.requirements), extra
        assert {n for n in names if n != "tabrion"} <= set(edge.blocked), extra
        assert edge.probe[0] and (SRC / "tabrion" / edge.probe[0]).is_file(), extra
        assert not edge.refuses or edge.hint, extra


def _copy_with(tmp_path: Path, file: str, line: str) -> Path:
    target = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", target / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    path = target / "tabrion" / file
    text = path.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    assert anchor in text, file
    path.write_text(text.replace(anchor, anchor + "\n" + line + "\n", 1), encoding="utf-8")
    return target


@pytest.mark.parametrize("extra", sorted(EDGES))
def test_a_top_level_import_of_the_extra_turns_the_edge_red(tmp_path: Path, extra: str) -> None:
    """Mutation probe: the copy with ``import <extra module>`` at the top of a module fails the edge's check."""
    edge = EDGES[extra]
    unchanged = run_python(check_source(edge), path=_copy_with(tmp_path / "control", edge.probe[0], ""))
    assert unchanged.returncode == 0, unchanged.stderr  # the copy itself passes (positive control)
    result = run_python(check_source(edge), path=_copy_with(tmp_path / "probe", *edge.probe))
    assert result.returncode != 0, f"the probe {edge.probe} went unnoticed"
    assert "ImportError" in result.stderr or "ModuleNotFoundError" in result.stderr
