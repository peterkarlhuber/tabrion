"""``docs/api-consumers.md`` (plan 07 K11): complete against contract section 8, consistent with the golden records,
and every named test exists."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "api-consumers.md"
GOLDEN = ROOT / "tests" / "golden"
SURFACE = ROOT / "docs" / "api" / "surface.json"

COVERAGE_HEADING = "## Coverage by public name"
NOT_COVERED_HEADING = "## Not covered by a consumer record yet"
CONSUMERS = ("Axentra", "Nodestro", "Orkexa")
STRICT_TEST = re.compile(r"tests/[\w/]+\.py::test_\w+")
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class ContractRow:
    place: str
    api: str


@dataclass(frozen=True)
class PageRow:
    api: str
    consumer: str
    golden: str
    tests: str


def _cells(line: str) -> list[str]:
    # Split on pipes outside backticks: the contract's cells hold `|` only inside code spans, if at all.
    cells, current, in_code = [], "", False
    for char in line.strip().strip("|"):
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append(current.strip())
            current = ""
        else:
            current += char
    cells.append(current.strip())
    return cells


def _ab_rows() -> list[ContractRow]:
    text = (ROOT / "docs" / "api" / "contract.md").read_text(encoding="utf-8")
    section = text[text.index("## 8. Compatibility table") : text.index("## 9. ")]
    rows = []
    for line in section.splitlines():
        cells = _cells(line)
        if len(cells) >= 3 and cells[1] in ("A", "B"):
            rows.append(ContractRow(place=cells[0], api=cells[2]))
    return rows


def _table(heading: str) -> list[list[str]]:
    page = PAGE.read_text(encoding="utf-8")
    body = page[page.index(heading) + len(heading) :]
    rows = []
    for line in body.splitlines()[1:]:
        if line.startswith("## "):
            break
        if line.startswith("|") and not line.startswith("|---"):
            rows.append(_cells(line))
    assert rows, heading
    return rows[1:]  # without the header row


def _coverage() -> list[PageRow]:
    rows = _table(COVERAGE_HEADING)
    assert all(len(cells) == 4 for cells in rows), rows
    return [PageRow(*cells) for cells in rows]


def _not_covered() -> list[PageRow]:
    rows = _table(NOT_COVERED_HEADING)
    assert all(len(cells) == 3 for cells in rows), rows
    return [PageRow(api=cells[0], consumer=cells[1], golden="", tests=cells[2]) for cells in rows]


def _public_names() -> set[str]:
    """Last component of every public module-level name (``tabrion.postgres.ping`` -> ``ping``), not members."""
    names = json.loads(SURFACE.read_text(encoding="utf-8"))["names"]
    result = set()
    for full in names:
        parts = full.split(".")
        if not any(part[:1].isupper() for part in parts[:-1]):
            result.add(parts[-1])
    return result


def _code_identifiers(text: str) -> set[str]:
    # Keyword arguments (``configure=...``) are not names of the API.
    spans = [re.sub(r"\w+=", "", span) for span in re.findall(r"`([^`]+)`", text)]
    return {name for span in spans for name in IDENTIFIER.findall(span)}


def _contract_names(row: ContractRow, public: set[str]) -> list[set[str]]:
    """The public names of the contract's API cell; one set per alternative ("`SessionLock` or `try_advisory_lock`")."""
    if row.api.startswith("same names"):
        return [(_code_identifiers(row.place) | _code_identifiers(row.api)) & public]
    return [_code_identifiers(alternative) & public for alternative in re.split(r"(?<=`) or (?=`)", row.api)]


def _tests(path: Path) -> dict[str, str]:
    """Top-level test functions of ``path`` with their source, decorators included."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            result[node.name] = "\n".join(lines[start - 1 : node.end_lineno])
    return result


def _references(text: str) -> list[str]:
    return [span for span in re.findall(r"`([^`]+)`", text) if "::" in span]


def test_contract_section_8_has_the_expected_rows() -> None:
    assert len(_ab_rows()) == 25


def test_every_category_a_b_row_of_section_8_is_mapped_to_its_api() -> None:
    public = _public_names()
    rows = _coverage() + _not_covered()
    problems = []
    for contract in _ab_rows():
        alternatives = _contract_names(contract, public)
        assert all(alternatives), f"no public Tabrion name in the contract's API cell: {contract.api}"
        matching = [row for row in rows if contract.place in row.consumer]
        if not matching:
            problems.append(f"no row quotes {contract.place}")
            continue
        listed: set[str] = set().union(*(_code_identifiers(row.api) for row in matching))
        if not any(names <= listed for names in alternatives):
            gaps = [names - listed for names in alternatives]
            smallest: set[str] = min(gaps, key=len)
            missing = sorted(smallest)
            problems.append(f"{contract.place}: {missing} missing from the API cells")
    assert not problems, problems


def test_every_api_cell_names_public_tabrion_names() -> None:
    surface = json.loads(SURFACE.read_text(encoding="utf-8"))["names"]

    def known(span: str) -> bool:
        return any(full == span or full.endswith("." + span) or full.startswith(span + ".") for full in surface)

    for row in _coverage() + _not_covered():
        spans = re.findall(r"`([^`]+)`", row.api)
        assert spans, row.api
        unknown = [span for span in spans if not known(span)]
        assert not unknown, f"{unknown} in {row.api}"


def test_every_golden_key_exists() -> None:
    for row in _coverage():
        current: dict[str, object] | None = None
        files = 0
        # Parenthesized remarks ("only `unavailable` so far") are not keys.
        for span in re.findall(r"`([^`]+)`", re.sub(r"\([^()]*\)", "", row.golden)):
            if span.endswith(".json"):
                path = GOLDEN / span
                assert path.is_file(), span
                data = json.loads(path.read_text(encoding="utf-8"))
                current = data.get("records", data)
                files += 1
            elif "/" in span:
                assert (ROOT / span).is_file(), span
            else:
                assert current is not None, f"key {span} before a golden file: {row.golden}"
                if span.endswith(".*"):
                    assert any(key.startswith(span[:-1]) for key in current), f"{span} in {row.golden}"
                else:
                    assert span in current, f"{span} in {row.golden}"
        assert files, row.golden


def test_every_coverage_row_has_a_replay_test_per_consumer() -> None:
    for row in _coverage():
        named = [STRICT_TEST.fullmatch(reference) for reference in _references(row.tests)]
        assert named and all(named), row.tests
        bodies = []
        for reference in _references(row.tests):
            path, name = reference.split("::")
            bodies.append(_tests(ROOT / path)[name].lower())
        for consumer in CONSUMERS:
            if consumer in row.consumer:
                assert any(consumer.lower() in body for body in bodies), f"{consumer}: {row.tests}"


def test_every_named_test_exists_and_has_the_strict_form() -> None:
    page = PAGE.read_text(encoding="utf-8")
    references = _references(page)
    assert len(references) >= 25
    bad = [reference for reference in references if not STRICT_TEST.fullmatch(reference)]
    assert not bad, bad
    for reference in references:
        path, name = reference.split("::")
        assert name in _tests(ROOT / path), reference


def test_the_page_is_in_the_documentation_site() -> None:
    assert "api-consumers.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
