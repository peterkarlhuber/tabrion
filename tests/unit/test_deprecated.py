"""TAB-051: the internal deprecation helper and its ``scripts/api_surface.py`` checks (policy S-3, S-6).

The acceptance test of the plan: a deprecated name gives a ``DeprecationWarning`` that names the replacement and the
version of removal. Tabrion 0.1 deprecates nothing, so the tests deprecate throwaway names and restore the registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import importlib.util
import inspect
import subprocess
import sys
from types import ModuleType
from typing import Any
import warnings

import pytest

from tabrion import _deprecated
from tabrion._deprecated import Deprecation, deprecated_callable, module_getattr, register
from tests.conftest import ROOT

MESSAGE = "tabrion.sql.old_split is deprecated since 0.2 and will be removed in 0.4; use tabrion.sql.split_statements"


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    saved = dict(_deprecated.REGISTRY)
    _deprecated.REGISTRY.clear()
    yield
    _deprecated.REGISTRY.clear()
    _deprecated.REGISTRY.update(saved)


def _old_split(sql: str, *, keep_empty: bool = False) -> list[str]:
    """Split ``sql``."""
    return [part for part in sql.split(";") if part or keep_empty]


def test_deprecated_callable_warns_with_replacement_and_target_version() -> None:
    wrapped = deprecated_callable(
        "tabrion.sql.old_split", replacement="tabrion.sql.split_statements", since="0.2", remove_in="0.4"
    )(_old_split)
    with pytest.warns(DeprecationWarning) as caught:
        assert wrapped("a;b") == ["a", "b"]
    (warning,) = caught
    assert str(warning.message) == MESSAGE + " instead"
    assert warning.filename == __file__, "the warning points at the caller, not at the helper"


def test_deprecated_callable_keeps_the_signature_and_the_docstring() -> None:
    wrapped = deprecated_callable(
        "tabrion.sql.old_split", replacement="tabrion.sql.split_statements", since="0.2", remove_in="0.4"
    )(_old_split)
    assert inspect.signature(wrapped) == inspect.signature(_old_split)
    assert wrapped.__doc__ == _old_split.__doc__
    assert wrapped.__name__ == "_old_split"


def test_deprecated_callable_warns_on_every_call() -> None:
    wrapped = deprecated_callable("tabrion.sql.old_split", replacement="x", since="0.2", remove_in="0.4")(_old_split)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        wrapped("a")
        wrapped("b")
    assert [w.category for w in caught] == [DeprecationWarning, DeprecationWarning]


def test_consumer_ci_with_error_filter_fails_on_use() -> None:
    """S-3: consumer CI runs with ``-W error::DeprecationWarning`` and notices the deprecation at once."""
    wrapped = deprecated_callable("tabrion.sql.old_split", replacement="x", since="0.2", remove_in="0.4")(_old_split)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with pytest.raises(DeprecationWarning, match=r"removed in 0\.4"):
            wrapped("a")


def _module_with_moved_name(fallback: Any = None) -> ModuleType:
    module = ModuleType("tabrion.probe")
    entry = register("tabrion.probe.OldName", replacement="tabrion.probe.NewName", since="0.2", remove_in="0.4")
    module.NewName = type("NewName", (), {})  # type: ignore[attr-defined]
    module.__getattr__ = module_getattr(  # type: ignore[method-assign]
        "tabrion.probe", {"OldName": (entry, lambda: module.NewName)}, fallback
    )
    return module


def test_moved_module_name_warns_and_returns_the_replacement() -> None:
    module = _module_with_moved_name()
    with pytest.warns(
        DeprecationWarning, match=r"^tabrion\.probe\.OldName .* removed in 0\.4; use tabrion\.probe\.NewName"
    ):
        assert module.OldName is module.NewName
    with pytest.raises(AttributeError, match="has no attribute 'Missing'"):
        _ = module.Missing


def test_moved_module_name_warns_at_the_callers_line() -> None:
    """Review L-1: both ``module.OldName`` and ``from module import OldName`` point at the consumer's line."""
    module = _module_with_moved_name()
    with pytest.warns(DeprecationWarning) as caught:
        _ = module.OldName
    assert caught[0].filename == __file__
    sys.modules["tabrion.probe"] = module
    try:
        with pytest.warns(DeprecationWarning) as caught:
            from tabrion.probe import OldName  # type: ignore[import-not-found]  # noqa: F401
        assert caught[0].filename == __file__
    finally:
        del sys.modules["tabrion.probe"]


def test_moved_module_name_keeps_an_existing_getattr() -> None:
    module = _module_with_moved_name(fallback=lambda name: f"lazy {name}")
    assert module.Other == "lazy Other"
    with pytest.warns(DeprecationWarning):
        _ = module.OldName


@pytest.mark.parametrize(
    ("since", "remove_in", "allowed"),
    [
        ("0.2", "0.4", True),
        ("0.2", "0.5", True),
        ("0.2", "0.3", False),  # the next minor: too early (S-3, the minor version after next)
        ("0.2", "0.2", False),
        ("0.9", "0.10", False),
        # 1.0 comes after every 0.x; that one more release lies between them is the release checklist's check.
        ("0.8", "1.0", True),
        ("0.9", "0.11", True),
        ("1.3", "2.0", True),  # from 1.0: removal is a major version (S-1)
        ("1.3", "1.5", False),
    ],
)
def test_register_enforces_the_s3_window(since: str, remove_in: str, allowed: bool) -> None:
    if allowed:
        assert (
            register("tabrion.x.y", replacement="tabrion.x.z", since=since, remove_in=remove_in).remove_in == remove_in
        )
    else:
        with pytest.raises(ValueError, match="too early"):
            register("tabrion.x.y", replacement="tabrion.x.z", since=since, remove_in=remove_in)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"path": "x.y", "replacement": "tabrion.z"}, "public tabrion path"),
        ({"path": "tabrion.x", "replacement": ""}, "replacement"),
        ({"path": "tabrion.x", "replacement": "tabrion.z", "since": "0.2.1"}, "MAJOR.MINOR"),
        ({"path": "tabrion.x", "replacement": "tabrion.z", "remove_in": "soon"}, "MAJOR.MINOR"),
    ],
)
def test_register_rejects_bad_input(kwargs: dict[str, str], match: str) -> None:
    arguments = {"since": "0.2", "remove_in": "0.4", **kwargs}
    with pytest.raises(ValueError, match=match):
        register(**arguments)


def test_register_rejects_a_different_second_registration() -> None:
    register("tabrion.x", replacement="tabrion.y", since="0.2", remove_in="0.4")
    with pytest.raises(ValueError, match="registered twice, differently"):
        register("tabrion.x", replacement="tabrion.z", since="0.2", remove_in="0.4")


def test_register_the_same_deprecation_again_is_idempotent() -> None:
    """Review L-4: a reloaded module registers its deprecation again; that must not make it unimportable."""
    first = register("tabrion.x", replacement="tabrion.y", since="0.2", remove_in="0.4")
    assert register("tabrion.x", replacement="tabrion.y", since="0.2", remove_in="0.4") is first
    assert list(_deprecated.REGISTRY) == ["tabrion.x"]


def test_deprecated_callable_refuses_a_class() -> None:
    """Review L-3: wrapping a class would turn it into a function."""
    decorate = deprecated_callable("tabrion.x.Old", replacement="tabrion.x.New", since="0.2", remove_in="0.4")
    with pytest.raises(TypeError, match="functions only"):
        decorate(type("Old", (), {}))


def test_deprecated_coroutine_function_warns_at_the_call() -> None:
    async def fetch(n: int) -> int:
        return n

    wrapped = deprecated_callable("tabrion.x.fetch", replacement="tabrion.x.get", since="0.2", remove_in="0.4")(fetch)
    with pytest.warns(DeprecationWarning, match="tabrion.x.fetch") as caught:
        coroutine = wrapped(3)
    assert caught[0].filename == __file__
    assert asyncio.run(coroutine) == 3
    if sys.version_info >= (3, 12):
        assert inspect.iscoroutinefunction(wrapped)


def test_the_helper_adds_no_public_name() -> None:
    """The frozen surface allows no new name (TAB-051 stays internal): an empty ``__all__``, a private module."""
    assert _deprecated.__all__ == []
    surface = (ROOT / "docs" / "api" / "surface.json").read_text(encoding="utf-8")
    assert "_deprecated" not in surface and "Deprecation" not in surface


def test_tabrion_0_1_deprecates_nothing() -> None:
    """Importing every public module registers no deprecation (a new one needs surface.json and the CHANGELOG)."""
    code = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import api_surface\n"
        "for name in api_surface.PUBLIC_MODULES:\n"
        "    importlib.import_module(name)\n"
        "from tabrion._deprecated import REGISTRY\n"
        "assert not REGISTRY, sorted(REGISTRY)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------------------------------------------------
# scripts/api_surface.py: the deprecation and CHANGELOG checks
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def surface_script() -> Any:
    spec = importlib.util.spec_from_file_location("api_surface_under_test", ROOT / "scripts" / "api_surface.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY: dict[str, Any] = {"kind": "function", "signature": "(sql: str) -> list[str]", "since": "0.1", "stability": "S"}
OLD = Deprecation("tabrion.sql.old_split", "tabrion.sql.split_statements", "0.2", "0.4")
CHANGELOG = """# Changelog

## [Unreleased]

### Deprecated

- `tabrion.sql.old_split`: use `split_statements` (removed in 0.4).

### Removed

- `sql.gone` (deprecated in 0.1).

### Changed

- `tabrion.sql.changed(`: a new default.

## [0.1.0] - 2026-11-01

### Added

- `tabrion.sql.split_statements`.
"""


def test_deprecation_consistent(surface_script: Any) -> None:
    recorded = {OLD.path: {**ENTRY, "deprecated": OLD.as_surface()}}
    assert surface_script.check_deprecations(recorded, {OLD.path: OLD}, CHANGELOG, (0, 2)) == []


@pytest.mark.parametrize(
    ("recorded", "changelog", "current", "message"),
    [
        ({}, CHANGELOG, (0, 2), "not in surface.json"),
        ({OLD.path: ENTRY}, CHANGELOG, (0, 2), "deprecation differs"),
        ({OLD.path: {**ENTRY, "deprecated": {**OLD.as_surface(), "remove_in": "0.5"}}}, CHANGELOG, (0, 2), "differs"),
        ({OLD.path: {**ENTRY, "deprecated": OLD.as_surface()}}, "## [Unreleased]\n", (0, 2), "'### Deprecated'"),
        ({OLD.path: {**ENTRY, "deprecated": OLD.as_surface()}}, CHANGELOG, (0, 4), "due for removal in 0.4"),
    ],
)
def test_deprecation_problems(
    surface_script: Any, recorded: dict[str, Any], changelog: str, current: tuple[int, int], message: str
) -> None:
    problems = surface_script.check_deprecations(recorded, {OLD.path: OLD}, changelog, current)
    assert any(message in p for p in problems), problems


def test_surface_deprecation_without_warning_code(surface_script: Any) -> None:
    recorded = {OLD.path: {**ENTRY, "deprecated": OLD.as_surface()}}
    (problem,) = surface_script.check_deprecations(recorded, {}, CHANGELOG, (0, 2))
    assert "the code does not warn" in problem


def test_changelog_names_every_change_since_the_base(surface_script: Any) -> None:
    base = {
        "tabrion.sql.gone": ENTRY,
        "tabrion.sql.changed": ENTRY,
        "tabrion.sql.old_split": ENTRY,
        "tabrion.sql.same": ENTRY,
    }
    recorded = {
        "tabrion.sql.changed": {**ENTRY, "signature": "(sql: str, x: int = 1) -> list[str]"},
        "tabrion.sql.old_split": {**ENTRY, "deprecated": OLD.as_surface()},
        "tabrion.sql.same": ENTRY,
    }
    assert surface_script.check_changelog(base, recorded, CHANGELOG) == []
    problems = surface_script.check_changelog(base, recorded, "## [Unreleased]\n\n### Added\n\n- nothing\n")
    assert len(problems) == 3, problems
    assert any("tabrion.sql.gone: removed" in p for p in problems)
    assert any("tabrion.sql.changed: changed" in p for p in problems)
    assert any("tabrion.sql.old_split: deprecated" in p for p in problems)


def test_changelog_mentions_count_only_in_unreleased_and_as_whole_names(surface_script: Any) -> None:
    base = {"tabrion.sql.split_statements": ENTRY}
    recorded = {"tabrion.sql.split_statements": {**ENTRY, "signature": "()"}}
    # Named by its last part only in [Unreleased], and in full only under a released version: neither counts.
    changelog = (
        "## [Unreleased]\n\n### Changed\n\n- `split_statements`\n\n"
        "## [0.1.0]\n\n### Changed\n\n- `sql.split_statements`\n"
    )
    (problem,) = surface_script.check_changelog(base, recorded, changelog)
    assert problem.startswith("tabrion.sql.split_statements: changed")


def test_base_mode_runs_against_git(surface_script: Any) -> None:
    """``--base HEAD`` compares the file with itself: consistent."""
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=False).returncode:
        pytest.skip("not a git checkout")
    assert surface_script.main(["--base", "HEAD"]) == 0


def test_base_mode_without_the_file_at_the_base(surface_script: Any) -> None:
    """A surface file that the base revision does not have yet counts as empty (every name is new there)."""
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=False).returncode:
        pytest.skip("not a git checkout")
    assert surface_script.load_base("HEAD", ROOT / "docs" / "api" / "no-such-surface.json") == {}


def _real_surface(surface_script: Any) -> dict[str, Any]:
    names: dict[str, Any] = surface_script.load(ROOT / "docs" / "api" / "surface.json")
    return names


def test_promoting_the_staged_names_is_an_addition(surface_script: Any) -> None:
    """Review M-1: the 0.2 release drops ``staged`` from every staged entry; ``--base <0.1>`` must stay consistent."""
    base = _real_surface(surface_script)
    staged = [path for path, entry in base.items() if "staged" in entry]
    assert staged, "surface.json has no staged name to promote"
    promoted = {path: {k: v for k, v in entry.items() if k != "staged"} for path, entry in base.items()}
    assert surface_script.check_changelog(base, promoted, "## [Unreleased]\n") == []
    # A stub that changed before its promotion and a staged name dropped again were never public either.
    changed = {**promoted, staged[0]: {**promoted[staged[0]], "signature": "() -> None"}}
    del changed[staged[1]]
    assert surface_script.check_changelog(base, changed, "## [Unreleased]\n") == []


def test_meta_changes_are_no_surface_change(surface_script: Any) -> None:
    base = {"tabrion.sql.split_statements": ENTRY}
    recorded = {"tabrion.sql.split_statements": {**ENTRY, "since": "0.2", "stability": "X"}}
    assert surface_script.check_changelog(base, recorded, "## [Unreleased]\n") == []


def test_added_does_not_cover_a_change(surface_script: Any) -> None:
    base = {"tabrion.sql.split_statements": ENTRY}
    recorded = {"tabrion.sql.split_statements": {**ENTRY, "signature": "()"}}
    changelog = "## [Unreleased]\n\n### Added\n\n- `tabrion.sql.split_statements`\n"
    (problem,) = surface_script.check_changelog(base, recorded, changelog)
    assert "changed since the base" in problem


def test_root_names_need_the_full_path(surface_script: Any) -> None:
    """Review L-5: `` `Database` `` does not name ``tabrion.Database``; `` `sqlite.connect` `` names its path."""
    assert not surface_script.named("tabrion.Database", "- `Database`: new default")
    assert surface_script.named("tabrion.Database", "- `tabrion.Database`: new default")
    assert surface_script.named("tabrion.sqlite.connect", "- `sqlite.connect()`: new default")
    assert surface_script.named("tabrion.Database.health", "- `Database.health`: new default")


@pytest.mark.parametrize("ref", ["nosuchref-ldp", "-x", "--output=/tmp/x", ""])
def test_base_mode_refuses_a_bad_revision(surface_script: Any, ref: str) -> None:
    """Review L-2: a bad or option-like revision is an error, never an empty (consistent) base."""
    with pytest.raises(SystemExit, match="is not a"):
        surface_script.load_base(ref, ROOT / "docs" / "api" / "surface.json")


def test_base_mode_refuses_a_surface_outside_the_repository(surface_script: Any, tmp_path: Any) -> None:
    with pytest.raises(SystemExit, match="inside the repository"):
        surface_script.load_base("HEAD", tmp_path / "surface.json")


def test_main_honors_base(surface_script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--base`` is not ignored: a base with a name the code no longer has fails the run (rc 1)."""
    base = {**_real_surface(surface_script), "tabrion.sql.ldp_removed": ENTRY}
    monkeypatch.setattr(surface_script, "load_base", lambda ref, surface: base)
    assert surface_script.main(["--base", "HEAD"]) == 1
    monkeypatch.setattr(surface_script, "load_base", lambda ref, surface: _real_surface(surface_script))
    assert surface_script.main(["--base", "HEAD"]) == 0
