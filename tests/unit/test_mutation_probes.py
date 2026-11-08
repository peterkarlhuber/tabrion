"""Mutation probes for TAB-010..TAB-012 (planning document 07 section 7): a copy of the package with one safety rule
broken must turn the named test red. A probe that stays green means the suite does not guard that rule.

Each probe replaces one exact piece of source (the anchor must exist, so a refactoring cannot silently disarm the
probe) and runs the named tests in a fresh interpreter against the mutated copy.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.conftest import ROOT, SRC

PROBES = [
    # R-NORETRY: a connection lost after sending must never be retryable.
    (
        "errors.py",
        "        retryable = False  # R-NORETRY: never repeat what may have run\n",
        "        pass\n",
        "tests/unit/test_errors.py::test_e01_outcome_unknown_and_no_retry_after_sending",
    ),
    # outcome_unknown must follow ``sent`` and ``during_commit``.
    (
        "errors.py",
        "kind is Kind.CONNECTION and bool(sent or during_commit)",
        "kind is Kind.CONNECTION and bool(sent)",
        "tests/unit/test_errors.py::test_e01_outcome_unknown_and_no_retry_after_sending",
    ),
    # 57014 with a statement timeout is TIMEOUT, not CANCELED.
    (
        "errors.py",
        "        if _STATEMENT_TIMEOUT_TEXT in text:\n",
        "        if False:\n",
        "tests/unit/test_errors.py::test_e01_e09_postgres_mapping",
    ),
    # The classification depth is 5, not more.
    (
        "errors.py",
        "_MAX_DEPTH = 5\n",
        "_MAX_DEPTH = 6\n",
        "tests/unit/test_errors.py::test_chains_cause_and_context_up_to_depth_five",
    ),
    # B-06-1: server text lines in a preformatted message must go.
    (
        "errors.py",
        "    if not any(marker in text for marker in _SUSPICIOUS):\n        return text\n",
        "    return text\n",
        "tests/unit/test_errors.py::test_e23_preformatted_messages_lose_server_text",
    ),
    # A formatter that formats exc_info itself must not see the original exception.
    (
        "errors.py",
        "found)\n            record.exc_info = None\n",
        "found)\n",
        "tests/unit/test_errors.py::test_e21_unique_violation_detail_is_gone_from_text_and_json",
    ),
    # A wrapper that quotes the database error must be scrubbed too.
    (
        "errors.py",
        "            te._str = _scrub(str(te._str), errors)  # type: ignore[attr-defined]\n",
        "            pass\n",
        "tests/unit/test_errors.py::test_e22_chained_exception",
    ),
    # H-1: __context__ is a fallback only; an outer connection loss is not overridden by an older handled error.
    (
        "errors.py",
        '                for name in ("orig", "__cause__"):\n',
        '                for name in ("orig", "__cause__", "__context__"):\n',
        "tests/unit/test_errors.py::test_h1_an_older_handled_error_never_classifies_a_driver_error",
    ),
    (
        "errors.py",
        "            if _is_connection_loss(first):\n",
        "            if False:\n",
        "tests/unit/test_errors.py::test_h1_outermost_connection_loss_wins_over_a_cause",
    ),
    # H-2: database errors inside containers and exception args.
    (
        "errors.py",
        "        if depth < _MAX_COLLECT_DEPTH:\n",
        "        if False:\n",
        "tests/unit/test_errors.py::test_h2_found_errors_inside_containers_set_the_record_attributes",
    ),
    # H-2: the escaped (repr) form of DETAIL.
    (
        "errors.py",
        '    text = _ESCAPED_SERVER_TEXT.sub("", text)\n',
        "",
        "tests/unit/test_errors.py::test_m3_inline_single_line_server_text_is_stripped",
    ),
    # M-2: continuation lines of a multi-line DETAIL.
    (
        "errors.py",
        "            skipping = True\n",
        "",
        "tests/unit/test_errors.py::test_m2_multi_line_detail_is_removed_completely",
    ),
    # Review M-3 mutant 10: notes on a driver error.
    (
        "errors.py",
        "            te.__notes__ = None\n",
        "            pass\n",
        "tests/unit/test_errors.py::test_m3_notes_on_a_driver_error_are_dropped",
    ),
    # Review M-3 mutant 18: exception group members.
    (
        "errors.py",
        "        if isinstance(members, (tuple, list)):\n            linked.extend(members)\n",
        "",
        "tests/unit/test_errors.py::test_m3_group_whose_only_database_error_is_a_member",
    ),
    # Review M-3 mutant 9: mapping arguments.
    (
        "errors.py",
        "            record.args = {k: safe_message(v) if _is_db_exc(v) else v for k, v in args.items()}\n",
        "            pass\n",
        "tests/unit/test_errors.py::test_m3_mapping_arguments_are_replaced",
    ),
    # Review M-3 mutant 11: an invalidated connection is never usable.
    (
        "errors.py",
        "        usable = False\n        if kind is Kind.UNKNOWN:\n",
        "        if kind is Kind.UNKNOWN:\n",
        "tests/unit/test_errors.py::test_m3_invalidated_connection_is_not_usable_whatever_the_kind",
    ),
    # Review L-2: outcome unknown by definition.
    (
        "errors.py",
        "    outcome_unknown = sqlstate in _OUTCOME_UNKNOWN_STATES or (\n",
        "    outcome_unknown = False or (\n",
        "tests/unit/test_errors.py::test_l2_outcome_unknown_by_definition",
    ),
    # Nested block comments.
    (
        "sql.py",
        '                if sql.startswith("/*", i):\n                    depth += 1\n',
        "                if False:\n                    depth += 1\n",
        "tests/unit/test_sql.py::test_q05_nested_block_comments",
    ),
    # E'...' strings take backslash escapes.
    (
        "sql.py",
        '                if escapes and sql[i] == "\\\\":\n',
        "                if False:\n",
        "tests/unit/test_sql.py::test_q04_quotes_and_escape_strings",
    ),
    # Comment-only statements are dropped.
    (
        "sql.py",
        "            if code:\n                out.append(sql[start:i].strip())\n",
        "            out.append(sql[start:i].strip())\n",
        "tests/unit/test_sql.py::test_q08_empty_and_comment_only_input",
    ),
    # The keyword conninfo password is masked.
    (
        "config.py",
        "            out += [text[last:value_start], _MASK]\n            last = i\n",
        "            pass\n",
        "tests/unit/test_config.py::test_c02_conninfo_password_is_masked",
    ),
    # A raw "@" in a URL password: everything up to the last "@" is user information.
    (
        "config.py",
        '    at = max(libpq_head.rfind("@"), head.rfind("@"))\n',
        '    at = max(libpq_head.find("@"), head.find("@"))\n',
        "tests/unit/test_config.py::test_c01_url_password_is_masked",
    ),
    # Review M-1: libpq's end of the user information.
    (
        "config.py",
        '    at = max(libpq_head.rfind("@"), head.rfind("@"))\n',
        '    at = head.rfind("@")\n',
        "tests/unit/test_config.py::test_c01_url_password_is_masked",
    ),
    # Review L-6: size limit.
    (
        "config.py",
        "            if size > _MAX_FILE_BYTES:\n",
        "            if False:\n",
        "tests/unit/test_config.py::test_c08_large_file_is_refused",
    ),
    # The secret file must be a regular file.
    (
        "config.py",
        "        if not stat.S_ISREG(os.fstat(fd).st_mode):\n",
        "        if False:\n",
        "tests/unit/test_config.py::test_c08_fifo_is_rejected_without_blocking"
        if hasattr(os, "mkfifo")
        else "tests/unit/test_config.py::test_c08_device_is_not_a_regular_file",
    ),
]


def _run(copy: Path, tests: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(copy), str(ROOT)])
    imported = subprocess.run(
        [sys.executable, "-c", "import tabrion.errors; print(tabrion.errors.__file__)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert str(copy) in imported, "the copy is not the package that gets imported"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-o", "addopts=", *tests],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _copy(tmp_path: Path) -> Path:
    copy = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", copy / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def test_unmutated_copy_passes(tmp_path: Path) -> None:
    """Positive control: the probed tests pass on an unchanged copy."""
    result = _run(_copy(tmp_path), sorted({p[3] for p in PROBES}))
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize(
    ("module", "anchor", "replacement", "test"),
    PROBES,
    ids=[p[3].split("::")[1] + f"-{i}" for i, p in enumerate(PROBES)],
)
def test_mutation_turns_the_test_red(tmp_path: Path, module: str, anchor: str, replacement: str, test: str) -> None:
    copy = _copy(tmp_path)
    target = copy / "tabrion" / module
    text = target.read_text(encoding="utf-8")
    assert text.count(anchor) == 1, f"probe anchor not found exactly once in {module}: {anchor!r}"
    target.write_text(text.replace(anchor, replacement), encoding="utf-8")
    result = _run(copy, [test])
    assert result.returncode == 1, f"mutation in {module} went unnoticed by {test}:\n{result.stdout[-2000:]}"
