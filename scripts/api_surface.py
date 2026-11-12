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
the path without ``tabrion.`` (``sqlite.connect``; not for root names such as ``tabrion.Database``) appears there in
backticks. Only the surface body is compared, not ``since``, ``stability`` or ``staged``; a name staged at the base
was not public there, so its promotion is an addition and needs no section.

``--no-stubs`` (TB-022, release check of TAB-040 and G-T2) also fails when a name the package version exports is a
stub: an entry with ``since`` at or below the package version that still carries ``staged`` (only a frozen stub, not
promoted), or a function, method, property or fixture whose body, after its docstring, is only
``raise NotImplementedError(...)``, ``pass`` or ``...``. The body is read from the source (``ast``), so nothing is
called; a name without Python source (a C function) is not a stub.

``--write`` regenerates the file from the code and keeps ``since``, ``stability`` and ``staged`` of every existing
entry; a new name gets the current minor version and ``S``. Review the diff: a changed signature after the API freeze
is an API change and needs the contract updated in the same pull request.

Usage: ``python scripts/api_surface.py [--write] [--base REF] [--no-stubs] [--surface docs/api/surface.json]``.
Needs every extra installed (``pip install -e ".[dev]"``), because it imports every public module.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import enum
import functools
import importlib
import inspect
import json
import os
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
    """``path`` in backticks, as a whole name; the form without ``tabrion.`` only when a dot remains.

    ``sqlite.connect`` names ``tabrion.sqlite.connect``, but a root name needs its full path (``tabrion.Database``):
    a bare `` `Database` `` could be about any ``Database`` (review L-5).
    """
    names = {path}
    short = path.removeprefix("tabrion.")
    if "." in short:
        names.add(short)
    return any(re.search(rf"`{re.escape(name)}(?:\(|`)", text) for name in names)


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

    def body(entry: Entry) -> Entry:
        return {k: v for k, v in entry.items() if k not in META}

    # A name staged at the base was not public there: its promotion (the minor release drops ``staged``) is an
    # addition, and a change of its stub or its removal is no API change (review M-1). Additions need no section.
    public = {path: entry for path, entry in base.items() if "staged" not in entry}
    problems: list[str] = []
    for path in sorted(set(public) - set(recorded)):
        if not named(path, text("Removed")):
            problems.append(f"{path}: removed since the base, but not named under '### Removed' in [Unreleased]")
    for path in sorted(set(public) & set(recorded)):
        old, new = body(public[path]), body(recorded[path])
        if old != new and not named(path, text("Changed", "Removed", "Security")):
            problems.append(f"{path}: changed since the base, but not named under '### Changed' in [Unreleased]")
        new_deprecation = "deprecated" in recorded[path] and "deprecated" not in base[path]
        if new_deprecation and not named(path, text("Deprecated")):
            problems.append(f"{path}: deprecated since the base, but not named under '### Deprecated'")
    return problems


# ---------------------------------------------------------------------------------------------------------------------
# Stubs (TB-022: no name of the released minor may still be a stub)
# ---------------------------------------------------------------------------------------------------------------------

CALLABLE_KINDS = ("function", "method", "classmethod", "staticmethod", "property", "fixture")


def _is_stub_statement(node: ast.stmt) -> bool:
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
        return True
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


@functools.cache
def _module_tree(filename: str) -> ast.Module:
    return ast.parse(Path(filename).read_text(encoding="utf-8"), filename=filename)


def _definition(fn: Any) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The ``def`` of ``fn`` in the parsed module (review L-1 of #46: a method is never dedented and parsed alone,
    because a triple-quoted string with flush-left lines cannot be dedented)."""
    code = getattr(fn, "__code__", None)
    try:
        filename = inspect.getsourcefile(fn)
    except TypeError:
        return None
    if code is None or filename is None or not Path(filename).is_file():
        return None
    for node in ast.walk(_module_tree(filename)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn.__name__:
            first = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
            if code.co_firstlineno in (first, node.lineno):
                return node
    return None


def stub_kind(fn: Any) -> str | None:
    """How ``fn`` is a stub, or ``None`` when it is not one.

    A stub's body, after its docstring, holds only ``raise NotImplementedError``, ``pass`` or ``...``, or nothing
    at all. ``fn`` is unwrapped first (``functools.wraps``); without Python source it is not a stub.
    """
    node = _definition(inspect.unwrap(fn))
    if node is None:
        return None
    body = node.body
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        body = body[1:]
    if not all(_is_stub_statement(statement) for statement in body):
        return None
    if any(isinstance(statement, ast.Raise) for statement in body):
        return "its body only raises NotImplementedError"
    if body:
        return "its body is only pass or ..."
    return "its body is only a docstring"


def is_stub(fn: Any) -> bool:
    """True when ``fn`` is a stub (:func:`stub_kind`)."""
    return stub_kind(fn) is not None


def _function_of(path: str, kind: str) -> Any:
    """The function behind a public callable path (the getter of a property, the function of a fixture)."""
    obj = resolve(path)
    if kind == "fixture":
        fixture = _fixture(obj)
        return fixture[0] if fixture is not None else obj
    if kind == "property":
        owner, _, name = path.rpartition(".")
        raw = inspect.getattr_static(resolve(owner), name)
        return raw.fget if isinstance(raw, property) else raw
    return getattr(obj, "__func__", obj)


def check_stubs(recorded: dict[str, Entry], current: tuple[int, int]) -> list[str]:
    """TB-022: every name the package version exports is implemented, not a stub and not only staged."""
    problems: list[str] = []
    for path, entry in sorted(recorded.items()):
        since = entry.get("since", "")
        if not re.fullmatch(r"\d+\.\d+", since) or minor(since) > current:
            continue
        if "staged" in entry:
            problems.append(f"{path}: since {since}, but only staged ({entry['staged']}); promote it or move 'since'")
            continue
        if entry.get("kind") not in CALLABLE_KINDS:
            continue
        try:
            fn = _function_of(path, entry["kind"])
        except LookupError:
            continue  # reported by check() as no longer public
        kind = stub_kind(fn)
        if kind is not None:
            problems.append(f"{path}: since {since}, but it is a stub ({kind})")
    return problems


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """``git -C ROOT <args>`` with the C locale (messages are never parsed, but stay readable in the error)."""
    env = {**os.environ, "LC_ALL": "C"}
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "-C", str(ROOT), *args],  # noqa: S607 - git from PATH, as in CI
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def load_base(ref: str, surface: Path) -> dict[str, Entry]:
    """``surface`` at the git revision ``ref``; an empty surface when the file did not exist there.

    An unknown revision, a revision that starts with ``-`` (it would reach git as an option) and a surface file
    outside the repository are errors (``SystemExit``), never an empty base (review L-2).
    """
    if not ref or ref.startswith("-"):
        raise SystemExit(f"api surface: --base {ref!r} is not a revision")
    try:
        relative = surface.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        raise SystemExit(f"api surface: --base needs the surface file inside the repository ({ROOT})") from None
    commit = _git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
    if commit.returncode != 0:
        raise SystemExit(f"api surface: --base {ref!r} is not a commit of this repository")
    sha = commit.stdout.strip()
    if _git("cat-file", "-e", f"{sha}:{relative}").returncode != 0:
        return {}
    shown = _git("show", f"{sha}:{relative}")
    if shown.returncode != 0:
        raise SystemExit(f"api surface: git show {sha}:{relative} failed: {shown.stderr.strip()}")
    names: dict[str, Entry] = json.loads(shown.stdout)["names"]
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
    parser.add_argument(
        "--no-stubs", action="store_true", help="release check (TB-022): no name of this minor may be a stub"
    )
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
    if args.no_stubs:
        problems += check_stubs(recorded, current)
    for problem in problems:
        print(f"api surface: {problem}", file=sys.stderr)
    staged = sum(1 for e in recorded.values() if minor(e.get("since", "0.0")) > current)
    print(
        f"api surface: {len(recorded)} names ({staged} staged for a later minor), package {current[0]}.{current[1]}: "
        + ("consistent" if not problems else f"{len(problems)} problem(s)")
        + (", no stubs" if args.no_stubs and not problems else "")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
