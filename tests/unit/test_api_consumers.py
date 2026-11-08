"""``docs/api-consumers.md`` (plan 07 K11): complete against contract section 8; every named test exists."""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "api-consumers.md"


def _ab_rows() -> list[str]:
    text = (ROOT / "docs" / "api" / "contract.md").read_text(encoding="utf-8")
    section = text[text.index("## 8. Compatibility table") : text.index("## 9. ")]
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[1] in ("A", "B"):
            rows.append(cells[0])
    return rows


def _tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def test_every_category_a_b_row_of_section_8_is_listed() -> None:
    page = PAGE.read_text(encoding="utf-8")
    rows = _ab_rows()
    assert len(rows) == 25
    missing = [row for row in rows if row not in page]
    assert not missing, missing


def test_every_named_test_exists() -> None:
    page = PAGE.read_text(encoding="utf-8")
    named = re.findall(r"`(tests/[\w/]+\.py)::(test_\w+)`", page)
    assert len(named) >= 25
    for path, name in named:
        assert name in _tests(ROOT / path), f"{path}::{name}"


def test_the_page_is_in_the_documentation_site() -> None:
    assert "api-consumers.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
