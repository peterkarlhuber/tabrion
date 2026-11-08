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
    # TM-04: a link followed while SQLite opened the file is caught after opening.
    (
        "    return os.path.normcase(os.path.realpath(opened)) == os.path.normcase(os.path.realpath(path))\n",
        "    return True\n",
        _T + "test_connect.py::test_s07_link_followed_while_opening_and_swapped_back",
        True,
        True,
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
