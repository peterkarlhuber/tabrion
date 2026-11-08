"""Mutation probes for layer B (TAB-018), in the style of ``tests/unit/test_mutation_probes.py``.

Each probe breaks one safety rule in a copy of the package; the named test must turn red. The anchors must exist
exactly once, so a refactoring cannot disarm a probe silently.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit.test_mutation_probes import _copy, _run

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

T = "tests/unit/test_database.py::"
POSIX_ONLY = {T + "test_b04_sqlite_path_checks_run_in_from_url"}
"""Probed tests that skip on Windows (a skipped test cannot turn red)."""

PROBES = [
    # Write transactions take the write lock at BEGIN (specification 3.2).
    ("_sa/events.py", '"BEGIN IMMEDIATE"', '"BEGIN"', T + "test_b07_transaction_begins_immediate"),
    # read() is enforced by SQLite, not by Tabrion.
    (
        "_sa/events.py",
        '        connection.exec_driver_sql("PRAGMA query_only = ON")\n',
        "",
        T + "test_b11_read_is_enforced_by_sqlite",
    ),
    # query_only goes off again after read().
    (
        "_sa/database.py",
        '            dbapi.execute("PRAGMA query_only = OFF")\n',
        "            pass\n",
        T + "test_b22_query_only_does_not_stick",
    ),
    # A driver error never escapes untranslated (its text and parameters stay in .original).
    (
        "_sa/database.py",
        "            error = _translate(exc, statement)\n",
        "            raise\n",
        T + "test_b18_no_parameter_value_in_errors",
    ),
    # A read-only profile refuses transaction().
    ("_sa/database.py", "if not read and self._profile.read_only:", "if False:", T + "test_b11_read_only_profile"),
    # Nesting in one thread is refused.
    ("_sa/database.py", 'if getattr(self._local, "active", False):', "if False:", T + "test_b10_nesting_is_refused"),
    # The deadline handler is removed after the statement.
    (
        "_sa/database.py",
        "        dbapi.set_progress_handler(None, _PROGRESS_STEPS)\n",
        "        pass\n",
        T + "test_b13_no_deadline_stays_behind",
    ),
    # check_path and the file creation run in from_url.
    (
        "_sa/database.py",
        "            _sqlite_creator(path, profile).close()\n",
        "            pass\n",
        T + "test_b04_sqlite_path_checks_run_in_from_url",
    ),
    # TLS: a weak sslmode for a remote host is refused.
    ("_sa/database.py", "if sslmode in _WEAK_SSLMODES or", "if False and", T + "test_b03_tls_violations"),
    # TLS: a remote host without sslmode gets require.
    (
        "_sa/database.py",
        '        return url.update_query_dict({"sslmode": wanted})\n',
        "        return url\n",
        T + "test_b03_tls_rules",
    ),
    # TB-040: R-NORETRY over a stored classification.
    (
        "errors.py",
        "            return replace(stored, outcome_unknown=True, retryable=False)\n",
        "            return stored\n",
        T + "test_tb040_during_commit_overrides_a_stored_classification",
    ),
]


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
    if test in POSIX_ONLY and os.name != "posix":
        pytest.skip("the probed test needs symbolic links and POSIX modes")
    copy = _copy(tmp_path)
    target = copy / "tabrion" / module
    text = target.read_text(encoding="utf-8")
    assert text.count(anchor) == 1, f"probe anchor not found exactly once in {module}: {anchor!r}"
    target.write_text(text.replace(anchor, replacement), encoding="utf-8")
    result = _run(copy, [test])
    assert result.returncode == 1, f"mutation in {module} went unnoticed by {test}:\n{result.stdout[-2000:]}"
