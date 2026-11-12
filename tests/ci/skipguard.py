"""Skip guard (planning document 07 section 4.3, G-T2 "skip lists per job", TODO TB-141): no test is skipped silently.

The suite's ``tests/conftest.py`` loads this plugin; it stays inactive unless ``--skip-guard`` or the variable
``TABRION_SKIP_GUARD`` selects a mode:

- ``enforce`` (also ``1``): at the end of the session every skip reason, and every ``xfail`` reason outside
  ``tests/examples``, must match a pattern of the reviewed allowlist ``tests/ci/skip-reasons.txt``; any other reason
  turns a green run red (exit status 1). The CI workflows set ``TABRION_SKIP_GUARD=1``.
- ``report``: the same summary, without changing the exit status (to see what a job skips).

Either mode prints the skipped node IDs with their reasons, so each job's log is that job's skip record. Non-strict
``xfail`` is the flaky-test marker of section 4.3, so it is checked like a skip. A strict ``xfail`` is checked as well
(it parks a failing test, and the release checklist of section 5 allows no new xfail without an entry), except under
``tests/examples/``: there it is a measured baseline deviation whose reason lives in the deviation table of that
suite, so it is listed but not checked.

Allowlist format: one Python regular expression per line, matched against the whole reason (``re.fullmatch``); lines
starting with ``#`` are comments, and every pattern has a comment above it that says why and in which jobs it skips.
Only pytest and the standard library: the packaging jobs load ``tests/conftest.py`` without Tabrion's extras.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

# pytest's own evaluation of the xfail marks tells strict from non-strict (the same function in pytest 6 to 9; the
# "lowest versions" job runs 8.0).
from _pytest.skipping import evaluate_xfail_marks
import pytest

ALLOWLIST = Path(__file__).with_name("skip-reasons.txt")
ENV = "TABRION_SKIP_GUARD"
# Node ID prefixes whose strict xfails are listed but not checked (the baseline deviations of the example apps).
STRICT_XFAIL_EXEMPT = ("tests/examples/",)
# pytest 8.0 writes ``reason: <msg>`` into ``wasxfail`` for an imperative ``pytest.xfail`` and for ``run=False``;
# later versions write ``<msg>`` (review of #51, R-3).
XFAIL_PREFIX = "reason: "
MODES = {"": "off", "0": "off", "off": "off", "1": "enforce", "enforce": "enforce", "report": "report"}


@dataclass(frozen=True)
class Pattern:
    line: int
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Event:
    nodeid: str
    kind: str  # "skip", "xfail" (non-strict) or "xfail strict"
    reason: str

    @property
    def checked(self) -> bool:
        """Whether the reason must match the allowlist: all but a strict xfail under ``STRICT_XFAIL_EXEMPT``."""
        return self.kind != "xfail strict" or not self.nodeid.startswith(STRICT_XFAIL_EXEMPT)


def parse_allowlist(text: str, source: str = "skip-reasons.txt") -> list[Pattern]:
    """The patterns of an allowlist; a pattern without a comment line directly above it is an error."""
    patterns: list[Pattern] = []
    commented = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            commented = False
            continue
        if line.startswith("#"):
            commented = True
            continue
        if not commented:
            raise ValueError(f"{source} line {number}: a pattern needs a comment above it (why, which jobs)")
        try:
            patterns.append(Pattern(number, re.compile(line)))
        except re.error as exc:
            raise ValueError(f"{source} line {number}: not a regular expression: {exc}") from None
    return patterns


def reason_of(report: pytest.CollectReport | pytest.TestReport) -> str:
    """The reason pytest shows for a skip or xfail: without the ``Skipped: `` or (pytest 8.0) ``reason: `` prefix."""
    wasxfail = getattr(report, "wasxfail", None)
    if wasxfail is not None:
        return str(wasxfail).removeprefix(XFAIL_PREFIX)
    longrepr = report.longrepr
    message = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr)
    return message.removeprefix("Skipped: ")


def _mode(config: pytest.Config) -> str:
    value = config.getoption("skip_guard") or os.environ.get(ENV, "")
    if value.strip().lower() not in MODES:
        raise pytest.UsageError(f"{ENV} / --skip-guard: expected one of off, enforce (1), report; got {value!r}")
    return MODES[value.strip().lower()]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("skip guard", "TB-141: fail on a skip reason missing from tests/ci/skip-reasons.txt")
    group.addoption(
        "--skip-guard",
        default=None,
        metavar="MODE",
        help=f"off, enforce or report (default: the variable {ENV}, else off)",
    )
    group.addoption(
        "--skip-guard-reasons",
        default=None,
        metavar="PATH",
        help="the allowlist (default: tests/ci/skip-reasons.txt)",
    )


def pytest_configure(config: pytest.Config) -> None:
    mode = _mode(config)
    if mode == "off":
        return
    path = Path(config.getoption("skip_guard_reasons") or ALLOWLIST)
    try:
        patterns = parse_allowlist(path.read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise pytest.UsageError(f"skip guard: cannot read the allowlist {path}: {exc}") from None
    except ValueError as exc:
        raise pytest.UsageError(f"skip guard: {exc}") from None
    config.pluginmanager.register(SkipGuard(mode, patterns), "tabrion-skip-guard")


class SkipGuard:
    def __init__(self, mode: str, patterns: list[Pattern]) -> None:
        self.mode = mode
        self.patterns = patterns
        self.events: list[Event] = []

    def allowed(self, reason: str) -> bool:
        return any(p.regex.fullmatch(reason) for p in self.patterns)

    def violations(self) -> list[Event]:
        return [e for e in self.events if e.checked and not self.allowed(e.reason)]

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
        report = yield
        wasxfail = getattr(report, "wasxfail", None)
        if wasxfail is not None:
            # An xfail that failed (skipped) or passed (non-strict XPASS); a strict XPASS is a failure already.
            reason = reason_of(report)
            xfailed = evaluate_xfail_marks(item)
            strict = xfailed is not None and xfailed.strict and xfailed.reason == reason
            self.events.append(Event(report.nodeid, "xfail strict" if strict else "xfail", reason))
        elif report.skipped:
            self.events.append(Event(report.nodeid, "skip", reason_of(report)))
        return report

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.events.append(Event(report.nodeid, "skip", reason_of(report)))

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.mode == "enforce" and self.violations() and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        tr = terminalreporter
        violations = self.violations()
        tr.section(f"skip guard ({self.mode})", sep="=", bold=True)
        checked = [e for e in self.events if e.checked]
        tr.write_line(
            f"{len(checked)} checked (skip or xfail), {len(self.events) - len(checked)} strict xfail in"
            f" {', '.join(STRICT_XFAIL_EXEMPT)} (listed only); allowlist {len(self.patterns)} patterns"
        )
        for event in self.events:
            mark = "NOT ALLOWED " if event in violations else ""
            tr.write_line(f"{mark}{event.kind.upper()} {event.nodeid}: {event.reason}")
        if violations:
            verdict = "the run fails" if self.mode == "enforce" else "report mode: the exit status is unchanged"
            tr.write_line(
                f"skip guard: {len(violations)} reason(s) match no pattern of tests/ci/skip-reasons.txt ({verdict});"
                " fix the test or add a reviewed pattern with a comment (why, which jobs)",
                red=True,
                bold=True,
            )
