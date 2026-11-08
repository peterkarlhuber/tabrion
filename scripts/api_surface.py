#!/usr/bin/env python3
"""Freeze Tabrion's public API: compare the code with ``docs/api/surface.json`` (TAB-023, G-T1 condition C-6).

Every public name gets an entry with its kind, its signature (parameters with kinds, annotations and defaults; fields
of a dataclass; members of an enum; bases of an exception), ``since`` (the minor version that introduced it) and
``stability`` (``S`` stable, ``X`` experimental; contract section 0). Check mode (the default, CI job "api surface")
fails when:

* a signature differs from the file (a parameter, a default, a keyword-only marker, a field, an enum value);
* a public name is missing from the file, or a name of the file is gone from the code;
* a name whose ``since`` is newer than the package version is importable from its public path (the 0.1.0 surface must
  not export a 0.2 name). Such a name is *staged*: ``staged`` says where its frozen stub lives (``module:qualname``),
  and its signature is compared there;
* a re-exported name (``alias_of``) is not the same object as its canonical path;
* a deprecation (``tabrion._deprecated.REGISTRY``, TAB-051) and the ``deprecated`` value of its entry differ, the
  CHANGELOG names it under no ``### Deprecated`` heading, or the package reached its removal version.

``--base REF`` (S-6 of the API stability policy, ``docs/development/api-stability.md``) also compares the file with
its state at the git revision ``REF``: a removed name must be named under ``### Removed``, a changed signature under
``### Changed`` (or ``### Removed``/``### Security``), and a new deprecation under ``### Deprecated`` in the
``[Unreleased]`` section of ``CHANGELOG.md``. A name counts as named when its full path (``tabrion.sqlite.connect``) or
the path without ``tabrion.`` (``sqlite.connect``) appears there in backticks.

``--write`` regenerates the file from the code and keeps ``since``, ``stability`` and ``staged`` of every existing
entry; a new name gets the current minor version and ``S``. Review the diff: a changed signature after the API freeze
is an API change and needs the contract updated in the same pull request.

Usage: ``python scripts/api_surface.py [--write] [--base REF] [--surface docs/api/surface.json]``. Needs every extra
installed (``pip install -e ".[dev]"``), because it imports every public module.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import importlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any
import warnings

ROOT = Path(__file__).resolve().parent.parent
SURFACE = ROOT / "docs" / "api" / "surface.json"
CHANGELOG = ROOT / "CHANGELOG.md"

PUBLIC_MODULES = (
    "tabrion",
    "tabrion.errors",
    "tabrion.sql",
    "tabrion.config",
    "tabrion.sqlite",
    "tabrion.postgres",
    "tabrion.postgres.roles",
    "tabrion.postgres.locks",
    "tabrion.check",
    "tabrion.migrations",
    "tabrion.testing",
    "tabrion.testing.profiles",
)
"""Contract section 0. Order matters only to break ties when choosing the canonical path of a re-exported name."""

PUBLIC_DUNDERS = ("__init__", "__enter__", "__exit__", "__call__")
SKIP = ("__version__",)
"""Changes with every release; not a signature."""

Entry = dict[str, Any]


# ---------------------------------------------------------------------------------------------------------------------
# Describing objects
# ---------------------------------------------------------------------------------------------------------------------


def _annotation(value: object) -> str:
    if value is inspect.Parameter.empty:
        return ""
    if isinstance(value, str):
        return value
    return inspect.formatannotation(value)


def _default(value: object) -> str:
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)


def describe_signature(fn: Any) -> str:
    """A canonical, Python-version independent text: ``(a: int, /, b: str = 'x', *, c: bool = False) -> None``."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    parts: list[str] = []
    star = False
    for i, param in enumerate(params):
        if param.kind is param.KEYWORD_ONLY and not star:
            parts.append("*")
        if param.kind in (param.KEYWORD_ONLY, param.VAR_POSITIONAL):
            star = True
        prefix = "*" if param.kind is param.VAR_POSITIONAL else "**" if param.kind is param.VAR_KEYWORD else ""
        text = prefix + param.name
        if param.annotation is not param.empty:
            text += f": {_annotation(param.annotation)}"
        if param.default is not param.empty:
            text += f" = {_default(param.default)}"
        parts.append(text)
        last_positional_only = i + 1 == len(params) or params[i + 1].kind is not param.POSITIONAL_ONLY
        if param.kind is param.POSITIONAL_ONLY and last_positional_only:
            parts.append("/")
    result = f"({', '.join(parts)})"
    if sig.return_annotation is not sig.empty:
        result += f" -> {_annotation(sig.return_annotation)}"
    return result


def _field(field: dataclasses.Field[Any]) -> str:
    text = f"{field.name}: {_annotation(field.type)}"
    if field.default is not dataclasses.MISSING:
        text += f" = {_default(field.default)}"
    elif field.default_factory is not dataclasses.MISSING:
        text += f" = <factory {getattr(field.default_factory, '__qualname__', field.default_factory)!s}>"
    return text


def _fixture(obj: Any) -> tuple[Any, str] | None:
    """(wrapped function, scope) for a pytest fixture, for pytest 8 and 9."""
    marker = getattr(obj, "_fixture_function_marker", None) or getattr(obj, "_pytestfixturefunction", None)
    if marker is None:
        return None
    wrapped = obj._get_wrapped_function() if hasattr(obj, "_get_wrapped_function") else obj
    return wrapped, str(marker.scope)


def describe(obj: Any) -> Entry:
    """Kind and signature of one public object (without since/stability)."""
    fixture = _fixture(obj)
    if fixture is not None:
        return {"kind": "fixture", "scope": fixture[1], "signature": describe_signature(fixture[0])}
    if isinstance(obj, ModuleType):
        return {"kind": "module"}
    if inspect.isclass(obj):
        if issubclass(obj, BaseException):
            return {"kind": "exception", "bases": [b.__qualname__ for b in obj.__bases__]}
        if issubclass(obj, enum.Enum):
            return {
                "kind": "enum",
                "bases": [b.__qualname__ for b in obj.__bases__],
                "members": [f"{m.name} = {m.value!r}" for m in obj],
            }
        entry: Entry = {"kind": "dataclass" if dataclasses.is_dataclass(obj) else "class"}
        if dataclasses.is_dataclass(obj):
            params = obj.__dataclass_params__  # type: ignore[attr-defined]
            entry["frozen"] = params.frozen
            entry["fields"] = [_field(f) for f in dataclasses.fields(obj)]
        return entry
    if inspect.isfunction(obj) or inspect.isbuiltin(obj):
        return {"kind": "function", "signature": describe_signature(obj)}
    # Private module segments differ between Python versions (pathlib.Path is pathlib._local.Path in 3.13).
    return {"kind": "alias", "value": re.sub(r"\b([a-z]\w*)\.(?:_\w+\.)+", r"\1.", str(obj))}


def describe_member(raw: Any) -> Entry | None:
    """A method, classmethod, staticmethod or property as found in a class ``__dict__``."""
    if isinstance(raw, property):
        return {"kind": "property", "signature": describe_signature(raw.fget)}
    if isinstance(raw, classmethod):
        return {"kind": "classmethod", "signature": describe_signature(raw.__func__)}
    if isinstance(raw, staticmethod):
        return {"kind": "staticmethod", "signature": describe_signature(raw.__func__)}
    if inspect.isfunction(raw):
        return {"kind": "method", "signature": describe_signature(raw)}
    return None


def class_members(cls: type) -> dict[str, Entry]:
    members: dict[str, Entry] = {}
    for name, raw in vars(cls).items():
        if name.startswith("_") and name not in PUBLIC_DUNDERS:
            continue
        entry = describe_member(raw)
        if entry is not None:
            members[name] = entry
    return members


# ---------------------------------------------------------------------------------------------------------------------
# Collecting the surface of the code
# ---------------------------------------------------------------------------------------------------------------------


def collect() -> dict[str, Entry]:
    """Every public path -> its description; re-exports point to the canonical path with ``alias_of``."""
    paths: dict[int, list[str]] = {}
    objects: dict[str, Any] = {}
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        for name in module.__all__:
            if name in SKIP:
                continue
            with warnings.catch_warnings():
                # A deprecated name stays in __all__ until its removal (S-3); describing it is no use of it.
                warnings.simplefilter("ignore", DeprecationWarning)
                obj = getattr(module, name)
            path = f"{module_name}.{name}"
            objects[path] = obj
            if not isinstance(obj, ModuleType):
                paths.setdefault(id(obj), []).append(path)
    surface: dict[str, Entry] = {}
    for path, obj in objects.items():
        group = paths.get(id(obj), [path])
        canonical = min(group, key=lambda p: (p.count("."), group.index(p)))
        if path != canonical:
            surface[path] = {"kind": "reexport", "alias_of": canonical}
            continue
        surface[path] = describe(obj)
        if inspect.isclass(obj) and not issubclass(obj, enum.Enum):
            for member, entry in class_members(obj).items():
                surface[f"{path}.{member}"] = entry
    return surface


def resolve(path: str) -> Any:
    """The object at a public dotted path, or raise ``LookupError``.

    Any successful attribute lookup counts, not only ``__all__``: ``from tabrion import Result`` works whenever
    ``tabrion.__getattr__`` (PEP 562) can produce the name, whether or not ``__all__`` lists it (G-T1 CC-1).
    Class members are looked up with inheritance, so a 0.2 mixin that is mixed in early counts as well.
    """
    for module_name in sorted(PUBLIC_MODULES, key=len, reverse=True):
        if path.startswith(module_name + "."):
            obj: Any = importlib.import_module(module_name)
            for part in path[len(module_name) + 1 :].split("."):
                try:
                    obj = getattr(obj, part)
                except (AttributeError, ImportError):
                    raise LookupError(path) from None
            return obj
    raise LookupError(path)


def resolve_staged(location: str) -> tuple[Any, bool]:
    """``module:Qual.name`` -> (object, is a class member)."""
    module_name, _, qualname = location.partition(":")
    obj: Any = importlib.import_module(module_name)
    parts = qualname.split(".")
    for part in parts:
        obj = vars(obj)[part] if inspect.isclass(obj) else getattr(obj, part)
    return obj, len(parts) > 1


def describe_staged(location: str) -> Entry:
    obj, is_member = resolve_staged(location)
    entry = describe_member(obj) if is_member else describe(obj)
    if entry is None:
        raise ValueError(f"{location} is not a method or property")
    return entry


# ---------------------------------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------------------------------


def minor(version: str) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"not a version: {version!r}")
    return int(match.group(1)), int(match.group(2))


def package_minor() -> tuple[int, int]:
    from tabrion._version import __version__

    return minor(__version__)


# ---------------------------------------------------------------------------------------------------------------------
# Check and write
# ---------------------------------------------------------------------------------------------------------------------

META = ("since", "stability", "staged", "deprecated")


def check(recorded: dict[str, Entry], actual: dict[str, Entry], current: tuple[int, int]) -> list[str]:
    problems: list[str] = []
    for path, entry in recorded.items():
        since = entry.get("since", "")
        if not re.fullmatch(r"\d+\.\d+", since) or entry.get("stability") not in ("S", "X"):
            problems.append(f"{path}: needs 'since' (MAJOR.MINOR) and 'stability' (S or X)")
            continue
        body = {k: v for k, v in entry.items() if k not in META}
        if minor(since) > current:
            if path in actual:
                problems.append(f"{path}: since {since}, but the {current[0]}.{current[1]} surface exports it")
                continue
            try:
                resolve(path)
            except LookupError:
                pass
            else:
                problems.append(f"{path}: since {since}, but it is importable from its public path")
            if "staged" not in entry:
                problems.append(f"{path}: since {since} and not exported, so it needs 'staged' (module:qualname)")
                continue
            try:
                staged = describe_staged(entry["staged"])
            except (ImportError, AttributeError, KeyError, ValueError) as exc:
                problems.append(f"{path}: staged stub {entry['staged']} not found ({exc!r})")
                continue
            if staged != body:
                problems.append(f"{path}: staged signature changed\n  surface.json: {body}\n  code:         {staged}")
            continue
        if path not in actual:
            problems.append(f"{path}: in surface.json but no longer public (removing a name is an API change)")
            continue
        if actual[path] != body:
            problems.append(f"{path}: changed\n  surface.json: {body}\n  code:         {actual[path]}")
        alias_of = body.get("alias_of")
        if alias_of is not None:
            try:
                if resolve(path) is not resolve(alias_of):
                    problems.append(f"{path}: is not the same object as {alias_of}")
            except LookupError:
                problems.append(f"{path}: alias of {alias_of}, which does not resolve")
    for path in sorted(set(actual) - set(recorded)):
        problems.append(f"{path}: new public name; run scripts/api_surface.py --write and set since/stability")
    return problems


def write(
    recorded: dict[str, Entry], actual: dict[str, Entry], current: tuple[int, int], registry: dict[str, Any]
) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    default_since = f"{current[0]}.{current[1]}"
    for path, body in actual.items():
        old = recorded.get(path, {})
        out[path] = {**body, "since": old.get("since", default_since), "stability": old.get("stability", "S")}
        if path in registry:
            out[path]["deprecated"] = registry[path].as_surface()
    for path, entry in recorded.items():
        if path not in actual and "staged" in entry and minor(entry["since"]) > current:
            meta = {k: entry[k] for k in META}
            out[path] = {**describe_staged(entry["staged"]), **meta}
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------------------------------------------------
# Deprecations and the CHANGELOG (TAB-051, policy S-3 and S-6)
# ---------------------------------------------------------------------------------------------------------------------


def registry() -> dict[str, Any]:
    """The deprecations registered while :func:`collect` imported the public modules."""
    from tabrion._deprecated import REGISTRY

    return dict(REGISTRY)


def changelog_sections(text: str, *, unreleased_only: bool) -> dict[str, str]:
    """``### Heading`` -> its text, joined over every release (or only ``[Unreleased]``)."""
    if unreleased_only:
        match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        text = match.group(1) if match else ""
    sections: dict[str, str] = {}
    for match in re.finditer(r"^### +(\w+)[^\n]*\n(.*?)(?=^##+ |\Z)", text, re.M | re.S):
        sections[match.group(1)] = sections.get(match.group(1), "") + match.group(2)
    return sections


def named(path: str, text: str) -> bool:
    """``path`` or ``path`` without ``tabrion.`` in backticks, as a whole name (not only its last part)."""
    short = path.removeprefix("tabrion.")
    return any(re.search(rf"`{re.escape(name)}(?:\(|`)", text) for name in {path, short})


def check_deprecations(
    recorded: dict[str, Entry], registered: dict[str, Any], changelog: str, current: tuple[int, int]
) -> list[str]:
    problems: list[str] = []
    deprecated_text = changelog_sections(changelog, unreleased_only=False).get("Deprecated", "")
    for path, entry in sorted(registered.items()):
        if path not in recorded:
            problems.append(f"{path}: deprecated, but not in surface.json (only a public name can be deprecated)")
            continue
        if recorded[path].get("deprecated") != entry.as_surface():
            problems.append(
                f"{path}: deprecation differs\n  surface.json: {recorded[path].get('deprecated')}\n"
                f"  code:         {entry.as_surface()}"
            )
        if not named(path, deprecated_text):
            problems.append(f"{path}: deprecated, but CHANGELOG.md names it under no '### Deprecated' heading")
        if current >= minor(entry.remove_in):
            problems.append(f"{path}: due for removal in {entry.remove_in}; remove it (CHANGELOG '### Removed')")
    for path, entry in sorted(recorded.items()):
        if "deprecated" in entry and path not in registered:
            problems.append(f"{path}: 'deprecated' in surface.json, but the code does not warn (tabrion._deprecated)")
    return problems


def check_changelog(base: dict[str, Entry], recorded: dict[str, Entry], changelog: str) -> list[str]:
    """S-6: every surface change since ``base`` is named in the matching ``[Unreleased]`` CHANGELOG section."""
    sections = changelog_sections(changelog, unreleased_only=True)

    def text(*names: str) -> str:
        return "".join(sections.get(name, "") for name in names)

    problems: list[str] = []
    for path in sorted(set(base) - set(recorded)):
        if not named(path, text("Removed")):
            problems.append(f"{path}: removed since the base, but not named under '### Removed' in [Unreleased]")
    for path in sorted(set(base) & set(recorded)):
        old = {k: v for k, v in base[path].items() if k != "deprecated"}
        new = {k: v for k, v in recorded[path].items() if k != "deprecated"}
        if old != new and not named(path, text("Changed", "Removed", "Security")):
            problems.append(f"{path}: changed since the base, but not named under '### Changed' in [Unreleased]")
        new_deprecation = "deprecated" in recorded[path] and "deprecated" not in base[path]
        if new_deprecation and not named(path, text("Deprecated")):
            problems.append(f"{path}: deprecated since the base, but not named under '### Deprecated'")
    return problems


def load_base(ref: str, surface: Path) -> dict[str, Entry]:
    """``surface`` at the git revision ``ref``; an empty surface when the file did not exist there."""
    relative = surface.resolve().relative_to(ROOT).as_posix()
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "-C", str(ROOT), "show", f"{ref}:{relative}"],  # noqa: S607 - git from PATH, as in CI
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if "does not exist" in result.stderr or "exists on disk, but not in" in result.stderr:
            return {}
        raise SystemExit(f"api surface: git show {ref}:{relative} failed: {result.stderr.strip()}")
    names: dict[str, Entry] = json.loads(result.stdout)["names"]
    return names


def load(path: Path) -> dict[str, Entry]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    names: dict[str, Entry] = data["names"]
    return names


def dump(path: Path, names: dict[str, Entry]) -> None:
    data = {
        "comment": (
            "Frozen public API of Tabrion (TAB-023). Generated by scripts/api_surface.py --write; since and stability "
            "are maintained by hand. Contract: docs/api/contract.md."
        ),
        "format": 1,
        "names": names,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--write", action="store_true", help="regenerate the file (keeps since/stability/staged)")
    parser.add_argument("--surface", type=Path, default=SURFACE, help="default: docs/api/surface.json")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG, help="default: CHANGELOG.md")
    parser.add_argument("--base", help="git revision: every surface change since it must be in the CHANGELOG (S-6)")
    args = parser.parse_args(argv)
    recorded = load(args.surface)
    actual = collect()
    registered = registry()
    current = package_minor()
    if args.write:
        dump(args.surface, write(recorded, actual, current, registered))
        print(f"api surface: wrote {args.surface} ({len(actual)} public paths)")
        return 0
    if not recorded:
        print(f"api surface: {args.surface} is missing or empty", file=sys.stderr)
        return 1
    changelog = args.changelog.read_text(encoding="utf-8") if args.changelog.exists() else ""
    problems = check(recorded, actual, current) + check_deprecations(recorded, registered, changelog, current)
    if args.base:
        problems += check_changelog(load_base(args.base, args.surface), recorded, changelog)
    for problem in problems:
        print(f"api surface: {problem}", file=sys.stderr)
    staged = sum(1 for e in recorded.values() if minor(e.get("since", "0.0")) > current)
    print(
        f"api surface: {len(recorded)} names ({staged} staged for a later minor), package {current[0]}.{current[1]}: "
        + ("consistent" if not problems else f"{len(problems)} problem(s)")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
