"""Mutation probes for layer B (TAB-018), in the style of ``tests/unit/test_mutation_probes.py``.

Each probe breaks one safety rule in a copy of the package; the named test must turn red. The anchors must exist
exactly once, so a refactoring cannot disarm a probe silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_mutation_probes import _copy, _run

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from tests.unit.test_database import POSIX

T = "tests/unit/test_database.py::"
POSIX_ONLY = {T + "test_b04_sqlite_path_checks_run_in_from_url"}
"""Probed tests that skip where not ``POSIX``: a skipped test cannot turn red, so their probes skip there too."""

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
    # check_path and the file creation run in from_url: the portable half, probed on every OS (review L-3 of #37) ...
    (
        "_sa/database.py",
        "            _sqlite_creator(path, profile).close()\n",
        "            pass\n",
        T + "test_b04_from_url_opens_the_file",
    ),
    # ... and the POSIX-only half (symbolic link, 0600).
    (
        "_sa/database.py",
        "            _sqlite_creator(path, profile).close()\n",
        "            pass\n",
        T + "test_b04_sqlite_path_checks_run_in_from_url",
    ),
    # safe_url shows a SQLite path as it is, not percent-encoded (Windows nightly of 546d254).
    (
        "_sa/database.py",
        "        return before + _printable_path(database) + after\n",
        "        return _config.safe_url(url.render_as_string(hide_password=True))\n",
        T + "test_b01_safe_url_shows_the_sqlite_path_as_it_is",
    ),
    # ... with what is not printable percent-encoded (review M-1 of #37) ...
    (
        "_sa/database.py",
        "        ch if ch.isprintable() else",
        "        ch if True else",
        T + "test_b01_safe_url_shows_the_sqlite_path_as_it_is",
    ),
    # ... and only after the URL is masked (review M-2 of #37) ...
    (
        "_sa/database.py",
        "        return before + _printable_path(database) + after\n",
        "        return _config.safe_url(before + _printable_path(database) + after)\n",
        T + "test_b01_safe_url_masks_first_and_then_shows_the_path",
    ),
    # ... at the one place of the placeholder (review L-1 of #37).
    (
        "_sa/database.py",
        "        if not found or slot in after:\n",
        "        if not found:\n",
        T + "test_b01_safe_url_placeholder_is_new_for_every_call",
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
    result = _run(_copy(tmp_path), sorted({p[3] for p in PROBES} - (set() if POSIX else POSIX_ONLY)))
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize(
    ("module", "anchor", "replacement", "test"),
    PROBES,
    ids=[p[3].split("::")[1] + f"-{i}" for i, p in enumerate(PROBES)],
)
def test_mutation_turns_the_test_red(tmp_path: Path, module: str, anchor: str, replacement: str, test: str) -> None:
    if not POSIX and test in POSIX_ONLY:
        pytest.skip("the probed test skips where os.name is not posix")
    copy = _copy(tmp_path)
    target = copy / "tabrion" / module
    text = target.read_text(encoding="utf-8")
    assert text.count(anchor) == 1, f"probe anchor not found exactly once in {module}: {anchor!r}"
    target.write_text(text.replace(anchor, replacement), encoding="utf-8")
    result = _run(copy, [test])
    assert result.returncode == 1, f"mutation in {module} went unnoticed by {test}:\n{result.stdout[-2000:]}"
