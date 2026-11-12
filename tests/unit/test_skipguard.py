"""TB-141 (planning document 07 section 4.3, G-T2): the skip guard ``tests/ci/skipguard.py`` and its allowlist.

Inner runs use ``pytester`` in-process with the plugin loaded by ``-p`` and a small allowlist of their own.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.ci import skipguard

ALLOW = "# test: why and which jobs\nallowed reason\nflaky: .+\n"
TESTS = """
import pytest

def test_ok():
    pass

@pytest.mark.skip(reason="allowed reason")
def test_allowed():
    pass

@pytest.mark.skipif(True, reason="{reason}")
def test_other():
    pass
"""


@pytest.fixture
def guarded(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """A pytester with the allowlist ``ALLOW`` in ``allow.txt``; the outer job's ``TABRION_SKIP_GUARD`` is removed."""
    monkeypatch.delenv(skipguard.ENV, raising=False)
    pytester.makefile(".txt", allow=ALLOW)
    return pytester


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest(
        "-p", "tests.ci.skipguard", "-p", "no:cacheprovider", "--skip-guard-reasons", "allow.txt", *args
    )


def test_listed_reasons_pass_and_the_summary_names_each_node(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(test_a=TESTS.format(reason="allowed reason"))
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(passed=1, skipped=2)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(
        [
            "*skip guard (enforce)*",
            "2 checked (skip or xfail), 0 strict xfail in tests/examples/ (listed only); allowlist 2 patterns",
            "SKIP test_a.py::test_allowed: allowed reason",
            "SKIP test_a.py::test_other: allowed reason",
        ]
    )
    result.stdout.no_fnmatch_line("*NOT ALLOWED*")


def test_an_unlisted_reason_fails_a_green_run(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(test_a=TESTS.format(reason="no server here"))
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(passed=1, skipped=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["NOT ALLOWED SKIP test_a.py::test_other: no server here", "skip guard: 1 reason(s) match no pattern*"]
    )


def test_the_whole_reason_must_match(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(test_a=TESTS.format(reason="allowed reason, and more"))
    assert _run(guarded, "--skip-guard=enforce").ret == pytest.ExitCode.TESTS_FAILED


def test_report_mode_keeps_the_exit_status(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(test_a=TESTS.format(reason="no server here"))
    result = _run(guarded, "--skip-guard=report")
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*skip guard (report)*", "NOT ALLOWED SKIP test_a.py::test_other*", "*report mode*"])


def test_off_by_default_and_on_with_the_variable(guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    guarded.makepyfile(test_a=TESTS.format(reason="no server here"))
    result = _run(guarded)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.no_fnmatch_line("*skip guard*")
    monkeypatch.setenv(skipguard.ENV, "1")
    assert _run(guarded).ret == pytest.ExitCode.TESTS_FAILED
    monkeypatch.setenv(skipguard.ENV, "report")
    assert _run(guarded).ret == pytest.ExitCode.OK
    # The option wins over the variable.
    assert _run(guarded, "--skip-guard=off").ret == pytest.ExitCode.OK


def test_a_bad_mode_or_allowlist_is_a_usage_error(guarded: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    guarded.makepyfile(test_a="def test_ok():\n    pass\n")
    monkeypatch.setenv(skipguard.ENV, "yes")
    assert _run(guarded).ret == pytest.ExitCode.USAGE_ERROR
    monkeypatch.delenv(skipguard.ENV)
    guarded.makefile(".txt", bad="uncommented\n")
    result = guarded.runpytest("-p", "tests.ci.skipguard", "--skip-guard=enforce", "--skip-guard-reasons", "bad.txt")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result = guarded.runpytest("-p", "tests.ci.skipguard", "--skip-guard=enforce", "--skip-guard-reasons", "no.txt")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_a_module_level_skip_is_checked(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        test_a="import pytest\npytest.importorskip('tabrion_no_such_module')\n\ndef test_x():\n    pass\n",
        test_b="def test_ok():\n    pass\n",
    )
    result = _run(guarded, "--skip-guard=enforce")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["NOT ALLOWED SKIP test_a.py: could not import 'tabrion_no_such_module'*"])


def test_non_strict_xfail_is_checked(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        test_a="""
import pytest

@pytest.mark.xfail(strict=False, reason="flaky: issue 1")
def test_flaky_fails():
    assert False

@pytest.mark.xfail(strict=False, reason="flaky: issue 2")
def test_flaky_passes():
    pass

def test_unlisted_flaky(request):
    request.applymarker(pytest.mark.xfail(reason="not listed"))
    assert False
"""
    )
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(xfailed=2, xpassed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "3 checked (skip or xfail), 0 strict xfail in tests/examples/ (listed only); allowlist 2 patterns",
            "XFAIL test_a.py::test_flaky_fails: flaky: issue 1",
            "XFAIL test_a.py::test_flaky_passes: flaky: issue 2",
            "NOT ALLOWED XFAIL test_a.py::test_unlisted_flaky: not listed",
        ]
    )


STRICT = """
import pytest

@pytest.mark.xfail(strict=True, reason="{reason}")
def test_strict():
    assert False

def test_imperative_strict(request):
    request.applymarker(pytest.mark.xfail(strict=True, reason="{reason}"))
    assert False
"""


def test_strict_xfail_is_checked_outside_tests_examples(guarded: pytest.Pytester) -> None:
    """Review of #51, R-1: a strict xfail parks a failing test, so its reason needs an allowlist entry too."""
    guarded.mkdir("tests")
    guarded.makepyfile(**{"tests/test_outside": STRICT.format(reason="parked regression")})
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(xfailed=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "2 checked (skip or xfail), 0 strict xfail in tests/examples/ (listed only); allowlist 2 patterns",
            "NOT ALLOWED XFAIL STRICT tests/test_outside.py::test_strict: parked regression",
            "NOT ALLOWED XFAIL STRICT tests/test_outside.py::test_imperative_strict: parked regression",
        ]
    )
    guarded.makepyfile(**{"tests/test_outside": STRICT.format(reason="flaky: listed")})
    result = _run(guarded, "--skip-guard=enforce")
    assert result.ret == pytest.ExitCode.OK
    result.stdout.no_fnmatch_line("*NOT ALLOWED*")


def test_strict_xfail_under_tests_examples_is_listed_only(guarded: pytest.Pytester) -> None:
    """The baseline deviations of the example apps keep their reasons in that suite's own deviation table."""
    guarded.mkdir("tests")
    guarded.mkdir("tests/examples")
    guarded.makepyfile(
        **{
            "tests/examples/test_example": STRICT.format(reason="baseline deviation")
            + "\n@pytest.mark.xfail(strict=False, reason='not listed')\ndef test_flaky():\n    assert False\n"
        }
    )
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(xfailed=3)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "1 checked (skip or xfail), 2 strict xfail in tests/examples/ (listed only); allowlist 2 patterns",
            "XFAIL STRICT tests/examples/test_example.py::test_strict: baseline deviation",
            "XFAIL STRICT tests/examples/test_example.py::test_imperative_strict: baseline deviation",
            "NOT ALLOWED XFAIL tests/examples/test_example.py::test_flaky: not listed",
        ]
    )
    result.stdout.no_fnmatch_line("NOT ALLOWED XFAIL STRICT*")


def test_imperative_and_notrun_xfail_reasons_have_no_prefix(guarded: pytest.Pytester) -> None:
    """Review of #51, R-3: pytest 8.0 reports ``reason: <msg>``, later versions ``<msg>``; the guard sees ``<msg>``."""
    guarded.makepyfile(
        test_a="""
import pytest

def test_imperative():
    pytest.xfail("flaky: imperative")

@pytest.mark.xfail(run=False, reason="not run")
def test_not_run():
    assert False
"""
    )
    result = _run(guarded, "--skip-guard=enforce")
    result.assert_outcomes(xfailed=2)
    result.stdout.fnmatch_lines(
        ["XFAIL test_a.py::test_imperative: flaky: imperative", "NOT ALLOWED XFAIL test_a.py::test_not_run: *not run"]
    )
    result.stdout.no_fnmatch_line("*: reason: *")


def test_reason_of_strips_the_prefixes() -> None:
    def report(**fields: Any) -> pytest.TestReport:
        return cast(pytest.TestReport, SimpleNamespace(**{"wasxfail": None, "longrepr": None, **fields}))

    assert skipguard.reason_of(report(wasxfail="reason: imperative")) == "imperative"
    assert skipguard.reason_of(report(wasxfail="reason: [NOTRUN] allowed")) == "[NOTRUN] allowed"
    assert skipguard.reason_of(report(wasxfail="imperative")) == "imperative"
    assert skipguard.reason_of(report(longrepr=("f.py", 1, "Skipped: why"))) == "why"


def test_a_failing_run_keeps_its_status(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        test_a="import pytest\n\ndef test_bad():\n    assert False\n\ndef test_s():\n    pytest.skip('x')\n"
    )
    assert _run(guarded, "--skip-guard=enforce").ret == pytest.ExitCode.TESTS_FAILED
    guarded.makepyfile(test_a="")
    assert _run(guarded, "--skip-guard=enforce").ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_parse_allowlist() -> None:
    assert [p.regex.pattern for p in skipguard.parse_allowlist("# why\na\nb\n\n# why\nc\n")] == ["a", "b", "c"]
    for bad in ("a\n", "# why\na\n\nb\n", "# why\n(\n"):
        with pytest.raises(ValueError, match="line"):
            skipguard.parse_allowlist(bad)


def test_the_repository_allowlist_is_well_formed() -> None:
    text = Path(skipguard.ALLOWLIST).read_text(encoding="utf-8")
    patterns = skipguard.parse_allowlist(text)
    assert patterns, "tests/ci/skip-reasons.txt has no patterns"
    duplicates = {p.regex.pattern for p in patterns if [q.regex.pattern for q in patterns].count(p.regex.pattern) > 1}
    assert not duplicates, f"listed twice: {sorted(duplicates)}"
    assert not [p.regex.pattern for p in patterns if p.regex.fullmatch("")], "a pattern matches an empty reason"


# Arbitrary reasons that no reviewed pattern may accept (review of #51, R-2): a pattern like ``.+``, ``[\s\S]*`` or
# an open tail on a listed prefix would disable the guard without any test failing.
ARBITRARY_REASONS = (
    "x",
    "TODO",
    "broken",
    "flaky",
    "skip",
    "temporarily disabled",
    "reason: anything",
    "POSIX only, and anything else",
    "PostgreSQL tests need one of anything at all",
    "PostgreSQL tests need one of TABRION_TEST_POSTGRES; broken",
    "the PostgreSQL extension 'vector' is unavailable and could not be activated; broken anyway",
    "the PostgreSQL extension 'vector' is unavailable and could not be activated (any text)",
    "could not import 'psycopg': anything",
)
# The real forms of the two formerly open patterns (src/tabrion/testing/postgres.py) must still pass.
REAL_REASONS = (
    "PostgreSQL tests need one of TABRION_TEST_POSTGRES",
    "PostgreSQL tests need one of A_URL, B_URL",
    "the PostgreSQL extension 'vector' is unavailable and could not be activated",
    "the PostgreSQL extension 'citext' is available and could not be activated (InsufficientPrivilege, SQLSTATE 42501)",
    "the PostgreSQL extension 'vector' is installed elsewhere and could not be activated (Error, SQLSTATE -)",
)


@pytest.mark.parametrize("reason", ARBITRARY_REASONS)
def test_the_repository_allowlist_rejects_arbitrary_reasons(reason: str) -> None:
    patterns = skipguard.parse_allowlist(Path(skipguard.ALLOWLIST).read_text(encoding="utf-8"))
    assert not [p.regex.pattern for p in patterns if p.regex.fullmatch(reason)]


@pytest.mark.parametrize("reason", REAL_REASONS)
def test_the_repository_allowlist_accepts_the_real_postgres_reasons(reason: str) -> None:
    patterns = skipguard.parse_allowlist(Path(skipguard.ALLOWLIST).read_text(encoding="utf-8"))
    assert [p.regex.pattern for p in patterns if p.regex.fullmatch(reason)]
