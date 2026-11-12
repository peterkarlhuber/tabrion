"""TB-141 (planning document 07 section 4.3, G-T2): the skip guard ``tests/ci/skipguard.py`` and its allowlist.

Inner runs use ``pytester`` in-process with the plugin loaded by ``-p`` and a small allowlist of their own.
"""

from __future__ import annotations

from pathlib import Path

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
            "2 skipped or non-strict xfail, 0 strict xfail; allowlist 2 patterns",
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


def test_non_strict_xfail_is_checked_and_strict_xfail_is_listed(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        test_a="""
import pytest

@pytest.mark.xfail(strict=True, reason="baseline deviation")
def test_strict():
    assert False

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
    result.assert_outcomes(xfailed=3, xpassed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "3 skipped or non-strict xfail, 1 strict xfail; allowlist 2 patterns",
            "XFAIL STRICT test_a.py::test_strict: baseline deviation",
            "XFAIL test_a.py::test_flaky_fails: flaky: issue 1",
            "XFAIL test_a.py::test_flaky_passes: flaky: issue 2",
            "NOT ALLOWED XFAIL test_a.py::test_unlisted_flaky: not listed",
        ]
    )


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
