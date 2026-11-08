"""K7/K8 (planning document 07 section 4.3): the flaky list ``tests/ci/flaky.txt`` is well formed and short."""

from __future__ import annotations

import datetime
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent.parent
FLAKY = ROOT / "tests" / "ci" / "flaky.txt"
ENTRY = re.compile(
    r"(tests/\S+\.py::\S+) \| (https://github\.com/peterkarlhuber/\S+/issues/\d+) \| (\d{4}-\d{2}-\d{2})"
)
RELEASE_LIMIT = 2
"""More entries than this block the next release (G-T2)."""


def entries(text: str) -> list[re.Match[str]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ENTRY.fullmatch(line.strip())
        assert match, f"tests/ci/flaky.txt line {number}: expected '<node ID> | <issue URL> | <YYYY-MM-DD>'"
        datetime.date.fromisoformat(match.group(3))
        found.append(match)
    return found


def test_flaky_list_is_well_formed() -> None:
    listed = entries(FLAKY.read_text(encoding="utf-8"))
    for match in listed:
        path = ROOT / match.group(1).split("::", 1)[0]
        assert path.is_file(), f"{match.group(1)}: no such test file"
    assert len({m.group(1) for m in listed}) == len(listed), "a test is listed twice"


def test_flaky_list_is_within_the_release_limit() -> None:
    assert len(entries(FLAKY.read_text(encoding="utf-8"))) <= RELEASE_LIMIT, "more than 2 flaky tests block G-T2"


def test_entry_parser() -> None:
    issue = "https://github.com/peterkarlhuber/tabrion/issues/1"
    good = f"tests/unit/test_flaky_list.py::test_entry_parser | {issue} | 2026-11-08"
    assert len(entries(f"# c\n\n{good}\n")) == 1
    for bad in (
        "tests/x.py::t",
        "tests/x.py::t | https://example.com/1 | 2026-11-08",
        good.replace("2026-11-08", "2026-13-08"),
    ):
        try:
            entries(bad)
        except (AssertionError, ValueError):
            continue
        raise AssertionError(f"accepted {bad!r}")
