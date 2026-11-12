"""``docs/program/traceability.md`` (G-T2 "TAB-T-001..300 present", plan 07 K2/K8): every numbered plan test ID has
one row, every named test exists, and every gap has a TODO entry."""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "program" / "traceability.md"
TODO = ROOT / "docs" / "TODO.md"

PLAN_IDS = (
    *(f"TAB-T-{n:03d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20, 21, 22, 23, 24, 25)),
    *(f"TAB-T-{n:03d}" for n in (101, 102, 110, 111, 112, 113, 120, 121, 122, 123, 130, 131, 132, 133, 140, 141)),
    *(f"TAB-T-{n:03d}" for n in (150, 151, 152, 160, 161, 162, 170, 180, 181, 182, 190, 191)),
    *(f"TAB-T-{n:03d}" for n in (200, 201, 202, 210, 211, 212, 300)),
)
"""Every numbered TAB-T ID cited in planning documents 02, 04 and 07 (all of them are in 07), as of 2026-11-12.

A new ID in the plan needs a row on the page and an entry here."""

STATUSES = ("Covered", "Covered (0.2)", "Partial", "Gap")
REFERENCE = re.compile(r"(tests/[\w/]+\.py)(?:::(\w+))?(?:::(\w+))?(?:\[[^\]`]*\])?")
"""A file, ``file::test``, or ``file::Class::test``; a parametrized ID (``[...]``) is checked by its function."""
CODE_SPAN = re.compile(r"`([^`]*)`")
TODO_REF = re.compile(r"\((TB-\d{3})\)$")


def _rows() -> list[list[str]]:
    text = PAGE.read_text(encoding="utf-8")
    body = text[text.index("## Table") : text.index("## Summary")]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        for line in body.splitlines()
        if line.startswith("| TAB-T-")
    ]
    assert all(len(row) == 4 for row in rows), [row for row in rows if len(row) != 4]
    return rows


def _tests(path: Path) -> set[str]:
    """``name`` for every top-level test function and ``Class::name`` for every test method of ``path``."""
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    names.add(f"{node.name}::{item.name}")
    return names


def test_every_plan_id_has_exactly_one_row_in_order() -> None:
    assert [row[0] for row in _rows()] == list(PLAN_IDS)


def test_every_status_is_known() -> None:
    for plan_id, _, _, status in _rows():
        assert TODO_REF.sub("", status).strip() in STATUSES, (plan_id, status)


def _references(plan_id: str, tests: str) -> list[tuple[str, str, str]]:
    """Every test reference of a Tests cell; anything that looks like one but does not parse fails (review L-2)."""
    outside = CODE_SPAN.sub("", tests)
    assert "tests/" not in outside and "::" not in outside, f"{plan_id}: a reference outside backticks: {tests}"
    references = []
    for span in CODE_SPAN.findall(tests):
        if not (span.startswith("tests/") or "::" in span or span.endswith(".py")):
            continue  # a name, a setting or a file outside tests/ (TAB-T-101)
        match = REFERENCE.fullmatch(span)
        assert match, f"{plan_id}: unreadable reference `{span}`"
        references.append((match.group(1), match.group(2) or "", match.group(3) or ""))
    return references


def test_every_reference_resolves() -> None:
    problems = []
    for plan_id, _, tests, _ in _rows():
        for path, first, second in _references(plan_id, tests):
            file = ROOT / path
            if not file.is_file():
                problems.append(f"{plan_id}: {path} does not exist")
                continue
            name = f"{first}::{second}" if second else first
            if name and name not in _tests(file):
                problems.append(f"{plan_id}: {path}::{name} does not exist")
    assert not problems, problems


def test_covered_and_partial_rows_name_a_test() -> None:
    for plan_id, _, tests, status in _rows():
        if not status.startswith("Gap"):
            named = [reference for reference in _references(plan_id, tests) if reference[1]]
            # TAB-T-101 is a lint rule, not a test: it names the configuration and the CI jobs instead.
            assert named or plan_id == "TAB-T-101", (plan_id, tests)


def test_partial_and_gap_rows_name_an_open_todo_entry() -> None:
    todo = TODO.read_text(encoding="utf-8")
    for plan_id, _, _, status in _rows():
        if status.startswith(("Partial", "Gap")):
            match = TODO_REF.search(status)
            assert match, (plan_id, status)
            entry = re.search(rf"^### {match.group(1)} ·(?:(?!^### ).)*?\*\*Status:\*\* ([^·\n]*)", todo, re.M | re.S)
            assert entry, (plan_id, match.group(1))
            # Review L-3: the entry must still be open, or the row should not be a gap any more.
            assert not entry.group(1).strip().startswith("Done"), (plan_id, match.group(1), entry.group(1))
        else:
            assert not TODO_REF.search(status), (plan_id, status)


def test_the_summary_counts_match_the_table() -> None:
    statuses = [TODO_REF.sub("", row[3]).strip() for row in _rows()]
    summary = PAGE.read_text(encoding="utf-8").split("## Summary", 1)[1]
    covered = statuses.count("Covered") + statuses.count("Covered (0.2)")
    assert f"**Covered:** {covered} IDs" in summary
    assert f"**Partial:** {statuses.count('Partial')} IDs" in summary
    assert f"**Gap:** {statuses.count('Gap')} IDs" in summary


def test_the_page_is_in_the_documentation_site() -> None:
    assert "program/traceability.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
