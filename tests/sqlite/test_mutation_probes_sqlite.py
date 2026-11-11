"""Mutation probes for TAB-013 and TAB-014 (planning document 07 section 7, the M2 style of
``tests/unit/test_mutation_probes.py``): a copy of the package with one SQLite safety rule broken must turn the named
test red. Each anchor must exist exactly once, so a refactoring cannot silently disarm a probe.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from tests.sqlite.conftest import POSIX, can_symlink
from tests.unit.test_mutation_probes import _copy, _run

_T = "tests/sqlite/"


def _symlinks_possible() -> bool:
    with tempfile.TemporaryDirectory() as directory:
        return can_symlink(Path(directory))


SYMLINKS = _symlinks_possible()

# (anchor, replacement, test, needs POSIX modes, needs symbolic links)
PROBES: list[tuple[str, str, str, bool, bool]] = [
    # TM-03: the mode of a new file must not depend on the umask (fchmod on the new descriptor).
    (
        "            os.fchmod(descriptor, mode)\n",
        "            pass\n",
        _T + "test_connect.py::test_s01_new_database_and_side_files_are_0600",
        True,
        False,
    ),
    # TM-04: O_EXCL (and O_NOFOLLOW) on creation, so a link planted after the check is never followed.
    (
        "    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY\n",
        "    flags = os.O_CREAT | os.O_WRONLY | _O_CLOEXEC | _O_BINARY\n",
        _T + "test_connect.py::test_s07_link_planted_before_creation",
        True,
        True,
    ),
    # TM-03: the side files are checked, not only the database file.
    (
        "    for candidate in (name, *(name + suffix for suffix in _SIDE_FILES)):\n",
        "    for candidate in (name,):\n",
        _T + "test_profile.py::test_s14_check_path_refuses_each_link",
        False,
        True,
    ),
    # TB-045: the last check resolves the directory only, never the checked name again.
    (
        "    checked = os.path.join(os.path.realpath(directory), base)\n",
        "    checked = os.path.realpath(path)\n",
        _T + "test_connect.py::test_tb045_the_last_check_does_not_follow_the_name",
        False,
        True,
    ),
    # TM-04: a link followed while SQLite opened the file is caught after opening.
    (
        "    return os.path.normcase(os.path.realpath(opened)) == os.path.normcase(checked)\n",
        "    return True\n",
        _T + "test_connect.py::test_s07_link_followed_while_opening_and_swapped_back",
        True,
        True,
    ),
    # TM-04, TB-045: the descriptor SQLite opened is the checked file.
    (
        "        if after is not None and _opened_checked_file(before, after, expected, sides):\n",
        "        if True:\n",
        _T + "test_connect.py::test_tb045_another_file_opened_by_sqlite_is_refused",
        True,
        False,
    ),
    # TB-045: only a reused descriptor, with nothing new opened, may stand for the checked file.
    (
        "    if unexplained:\n",
        "    if False:\n",
        _T + "test_connect.py::test_tb045_another_file_opened_by_sqlite_is_refused",
        True,
        False,
    ),
    # TB-045: a descriptor SQLite reuses for the checked file is accepted (pools would fail otherwise).
    (
        "    return held\n",
        "    return False\n",
        _T + "test_connect.py::test_tb045_a_descriptor_sqlite_reuses_is_accepted",
        True,
        False,
    ),
    # TB-045: only the database's own side files opened meanwhile are ignored, nothing else.
    (
        "    unexplained = [entry for entry in new if entry[1:] not in side_files]\n",
        "    unexplained: list[tuple[int, int, int]] = []\n",
        _T + "test_connect.py::test_tb045_side_files_opened_meanwhile_do_not_hide_a_reused_descriptor",
        False,
        False,
    ),
    # TB-045 review F2: a side file counts only with a single link (nothing else linked under its name).
    (
        "            and info.st_nlink == 1\n",
        "",
        _T + "test_connect.py::test_tb045_side_file_identities_need_one_link_and_the_owners_rules",
        True,
        False,
    ),
    # TB-045 review F2: a side file counts only when it was the same before and after the open.
    (
        "            sides &= _side_file_identities(name, allow_shared_file=allow_shared_file)\n",
        "            sides = _side_file_identities(name, allow_shared_file=allow_shared_file)\n",
        _T + "test_connect.py::test_tb045_a_side_file_that_appeared_during_the_open_is_not_exempt",
        True,
        False,
    ),
    # TB-045 review F6: concurrent connects cannot interleave between the snapshots.
    (
        "        with _OPEN_LOCK:\n            before = _open_files() if name is not None else None\n",
        "        with contextlib.nullcontext():\n            before = _open_files() if name is not None else None\n",
        _T + "test_connect.py::test_tb045_connects_do_not_interleave_between_the_snapshots",
        True,
        False,
    ),
    # TB-045 review F6: only regular files are compared (a pipe or socket opened meanwhile is not a mismatch).
    (
        "        if stat.S_ISREG(info.st_mode):\n            files.add((descriptor, info.st_dev, info.st_ino))\n",
        "        files.add((descriptor, info.st_dev, info.st_ino))\n",
        _T + "test_connect.py::test_tb045_a_pipe_opened_meanwhile_does_not_count",
        True,
        False,
    ),
    # TB-045 review F4: a listing counts only when a probe descriptor shows up in it.
    (
        "            found = _complete_listing(candidates, probe.fileno())\n",
        "            found = candidates[0]\n",
        _T + "test_connect.py::test_tb045_an_incomplete_listing_is_not_used",
        True,
        False,
    ),
    # TB-045 review F1: a name that is gone while it is checked is checked again, never opened unchecked.
    (
        "            except FileNotFoundError:\n"
        '                _log.debug("the database file %s disappeared while it was checked", name)\n',
        "            except FileNotFoundError:\n                return 0, 0\n",
        _T + "test_connect.py::test_tb045_a_name_gone_while_checked_is_checked_again",
        True,
        False,
    ),
    # TB-045 review F10: the -wal and -journal files are never exempt in the reuse case.
    (
        '_EXEMPT_SIDE_FILES = ("-shm",)\n',
        '_EXEMPT_SIDE_FILES = ("-shm", "-wal", "-journal")\n',
        _T + "test_connect.py::test_tb045_a_new_descriptor_on_the_wal_or_journal_is_retried",
        True,
        False,
    ),
    # TB-045 review F11: only a permission error is a refusal; other errors are raised as they are.
    (
        "            if exc.errno in _DENIED:\n",
        "            if True:\n",
        _T + "test_connect.py::test_tb045_resource_errors_are_not_refusals",
        True,
        False,
    ),
    # TB-045 review F1/F13: a file whose identity cannot be taken is refused before SQLite opens anything.
    (
        '                raise _unsafe("sqlite.unsafe_path", path) from None'
        "  # unreadable: SQLite could not open it either\n",
        "                return 0, 0\n",
        _T + "test_connect.py::test_tb045_an_unreadable_file_is_refused",
        True,
        False,
    ),
    # TB-045 review F5/F13: the identity check runs under the open lock.
    (
        "        with _OPEN_LOCK:\n            if not os.path.lexists(name):\n",
        "        with contextlib.nullcontext():\n            if not os.path.lexists(name):\n",
        _T + "test_connect.py::test_tb045_the_identity_check_runs_under_the_lock",
        True,
        False,
    ),
    # TB-045 review F2/F13: a side file of another user is not exempt.
    (
        "            and info.st_uid == os.geteuid()\n",
        "",
        _T + "test_connect.py::test_tb045_side_file_identities_need_one_link_and_the_owners_rules",
        True,
        False,
    ),
    # TB-045 review F12: the listed probe must be the same file, not just a listed number.
    (
        "            if (listed.st_dev, listed.st_ino) == (info.st_dev, info.st_ino):\n",
        "            if True:\n",
        _T + "test_connect.py::test_tb045_a_listing_must_show_the_probe_as_the_same_file",
        True,
        False,
    ),
    # TB-045 review F14: a failing listing after the probe is raised, never taken as "no listing".
    (
        "    names = os.listdir(directory)\n",
        "    try:\n        names = os.listdir(directory)\n    except OSError:\n        return None\n",
        _T + "test_connect.py::test_tb045_a_failing_listing_is_raised",
        True,
        False,
    ),
    # TB-045 review F14: the connection is closed when the second listing fails.
    (
        "            except BaseException:\n                connection.close()\n                raise\n",
        "            except BaseException:\n                raise\n",
        _T + "test_connect.py::test_tb045_a_failing_listing_is_raised",
        True,
        False,
    ),
    # TB-045 review F16: a probe that could not run is raised and not cached as "no listing".
    (
        "        with tempfile.TemporaryFile() as probe:\n"
        "            found = _complete_listing(candidates, probe.fileno())\n",
        "        try:\n"
        "            with tempfile.TemporaryFile() as probe:\n"
        "                found = _complete_listing(candidates, probe.fileno())\n"
        "        except OSError:\n"
        "            found = None\n",
        _T + "test_connect.py::test_tb045_a_probe_that_cannot_run_is_not_cached",
        True,
        False,
    ),
    # TB-045 review F16: running out of descriptors while listing a candidate is no verdict on it.
    (
        "                if exc.errno in _EXHAUSTED:\n",
        "                if False:\n",
        _T + "test_connect.py::test_tb045_a_probe_that_cannot_run_is_not_cached",
        True,
        False,
    ),
    # TM-04: the file that was checked is the file that was opened (same inode).
    (
        "    if expected[1] and (info.st_dev, info.st_ino) != expected:\n        return False\n",
        "    if False:\n        return False\n",
        _T + "test_connect.py::test_s07_file_replaced_between_check_and_open",
        True,
        False,
    ),
    # DI-01: foreign keys on every connection.
    (
        "        f\"PRAGMA foreign_keys = {'ON' if profile.foreign_keys else 'OFF'}\",\n",
        '        "PRAGMA foreign_keys = OFF",\n',
        _T + "test_connect.py::test_s10_foreign_keys_are_enforced",
        False,
        False,
    ),
    # The profile refuses a group- or world-readable mode without allow_shared_file.
    (
        "        if mode & 0o077 and not self.allow_shared_file:\n",
        "        if False:\n",
        _T + "test_connect.py::test_profile_validation",
        False,
        False,
    ),
    # DI-02: BEGIN IMMEDIATE, so read-then-write in five threads loses no update (probes q.py, q2.py).
    (
        '    connection.execute("BEGIN IMMEDIATE")\n',
        '    connection.execute("BEGIN")\n',
        _T + "test_transactions.py::test_s16_lost_update_five_threads",
        False,
        False,
    ),
    # A KeyboardInterrupt in the block rolls back (BaseException, not Exception).
    (
        "        yield connection\n    except BaseException:\n        _rollback(connection)\n",
        "        yield connection\n    except Exception:\n        _rollback(connection)\n",
        _T + "test_transactions.py::test_s21_keyboard_interrupt_rolls_back",
        False,
        False,
    ),
    # query_only is reset after a read transaction.
    (
        '            connection.execute("PRAGMA query_only = OFF")\n',
        "            pass\n",
        _T + "test_transactions.py::test_s20_query_only_is_off_afterwards",
        False,
        False,
    ),
    # Contract I-26: the version is read again inside the write transaction (two processes apply a step once).
    (
        "            found = _user_version(connection)\n",
        "            found = current\n",
        _T + "test_migrate.py::test_s30_a_concurrent_migration_is_seen",
        False,
        False,
    ),
    # TAB-T-S31 / TAB-T-122: the backup's temporary file is 0600.
    (
        "    created = _create(temporary, _PRIVATE_MODE)\n",
        "    created = _create(temporary, 0o644)\n",
        _T + "test_backup.py::test_s31_backup_is_0600",
        True,
        False,
    ),
    # TAB-T-S32 / TAB-T-201: an aborted backup leaves no temporary file.
    (
        "                os.unlink(leftover)\n",
        "                pass\n",
        _T + "test_backup.py::test_s32_abort_leaves_no_half_target",
        False,
        False,
    ),
    # Without overwrite, a target that appeared during the copy is never replaced.
    (
        "        _publish(temporary, destination, overwrite=overwrite)\n",
        "        _publish(temporary, destination, overwrite=True)\n",
        _T + "test_backup.py::test_target_appearing_during_the_copy_is_not_replaced",
        False,
        False,
    ),
    # TAB-T-S37: one row more than asked tells whether the messages were truncated.
    (
        '            rows = _rows(reading, f"PRAGMA {pragma}({limit + 1})")\n',
        '            rows = _rows(reading, f"PRAGMA {pragma}({limit})")\n',
        _T + "test_checks.py::test_s37_integrity_messages_and_truncation",
        False,
        False,
    ),
    # TAB-T-S38: the table name is quoted.
    (
        'f"PRAGMA foreign_key_check({_quote(table)})"',
        'f"PRAGMA foreign_key_check({table})"',
        _T + "test_checks.py::test_s38_table_name_is_quoted",
        False,
        False,
    ),
    # T-NOTX: reads work whatever the caller's row_factory.
    (
        "        cursor.row_factory = None\n",
        "        pass\n",
        _T + "test_notx.py::test_readers_keep_the_state",
        False,
        False,
    ),
    # Review M-1 (TM-03): an existing file owned by another user is refused.
    (
        "    if info.st_uid != os.geteuid():\n",
        "    if False:\n",
        _T + "test_connect.py::test_m1_foreign_owner_is_refused",
        True,
        False,
    ),
    # Review M-1: an existing group- or world-writable file is refused.
    (
        "    if info.st_mode & _SHARED_WRITE:\n",
        "    if False:\n",
        _T + "test_connect.py::test_m1_group_or_world_writable_file_is_refused",
        True,
        False,
    ),
    # Review M-1: the existing side files follow the same rules.
    (
        "        _check_side_files(name, allow_shared_file=profile.allow_shared_file)\n",
        "        pass\n",
        _T + "test_connect.py::test_m1_side_file_owner_and_mode",
        True,
        False,
    ),
    # Review L-3 (mutant 4): the path is checked again after the profile was applied.
    (
        "            if not memory:\n                check_path(name)\n",
        "            if not memory:\n                pass\n",
        _T + "test_connect.py::test_l3_link_planted_while_the_profile_is_applied",
        False,
        True,
    ),
    # Review M-3: the copy SQLite opened is the temporary file that was created, before a page is written.
    (
        "            if not _same_file(temporary, created, copy):\n",
        "            if False:\n",
        _T + "test_backup.py::test_m3_file_replaced_before_sqlite_opens_the_copy",
        False,
        False,
    ),
    # Review M-3: the copy is checked again when it is synced, before it is published.
    (
        "        if expected[1] and expected != (info.st_dev, info.st_ino):\n",
        "        if False:\n",
        _T + "test_backup.py::test_m3_copy_replaced_before_it_is_published",
        False,
        False,
    ),
    # Review L-3 (mutant 15): an existing target is refused before the copy starts.
    (
        "    if os.path.lexists(destination) and not overwrite:\n",
        "    if False:\n",
        _T + "test_backup.py::test_l3_existing_target_is_refused_before_the_copy",
        False,
        False,
    ),
    # Review L-2: an idle connection with driver-managed transactions can run the checks.
    (
        "    if connection.in_transaction or not _is_autocommit(connection):\n",
        "    if connection.in_transaction:\n",
        _T + "test_checks.py::test_l2_checks_on_a_legacy_connection",
        False,
        False,
    ),
    # Review M-2 (TM-03): a world-writable directory without the sticky bit is reported.
    (
        "    if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:\n",
        "    if False:\n",
        _T + "test_checks.py::test_m2_world_writable_directory_without_sticky_bit",
        True,
        False,
    ),
    # #4 re-review DL-1: a group- or other-writable directory warns whatever the sticky bit.
    (
        "    if mode & (stat.S_IWOTH | stat.S_IWGRP):\n",
        "    if False:\n",
        _T + "test_checks.py::test_m2_world_writable_directory_without_sticky_bit",
        True,
        False,
    ),
]


def _skip_reason(posix: bool, links: bool) -> str | None:
    if posix and not POSIX:
        return "the probed test needs POSIX file modes"
    if links and not SYMLINKS:
        return "the probed test needs symbolic links"
    return None


def test_unmutated_copy_passes(tmp_path: Path) -> None:
    """Positive control: the probed tests pass on an unchanged copy."""
    tests = sorted({p[2] for p in PROBES if _skip_reason(p[3], p[4]) is None})
    result = _run(_copy(tmp_path), tests)
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize(
    ("anchor", "replacement", "test", "posix", "links"),
    PROBES,
    ids=[p[2].split("::")[1] + f"-{i}" for i, p in enumerate(PROBES)],
)
def test_mutation_turns_the_test_red(
    tmp_path: Path, anchor: str, replacement: str, test: str, posix: bool, links: bool
) -> None:
    copy = _copy(tmp_path)
    target = copy / "tabrion" / "sqlite.py"
    text = target.read_text(encoding="utf-8")
    assert text.count(anchor) == 1, f"probe anchor not found exactly once: {anchor!r}"
    reason = _skip_reason(posix, links)
    if reason is not None:
        pytest.skip(reason)
    target.write_text(text.replace(anchor, replacement), encoding="utf-8")
    result = _run(copy, [test])
    assert result.returncode == 1, f"mutation went unnoticed by {test}:\n{result.stdout[-2000:]}"
