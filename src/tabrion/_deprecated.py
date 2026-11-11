"""Internal deprecation helper (TAB-051; API stability policy S-3, ``docs/development/api-stability.md``).

Not public: nothing here is in an ``__all__``, and ``docs/api/surface.json`` has no entry for it. A stable name is
deprecated in three steps, all in the same pull request:

1. wrap it: :func:`deprecated_callable` for a function or method, :func:`module_getattr` for a module-level name
   that moved (the old name leaves the module namespace and is served by PEP 562 ``__getattr__``);
2. add ``"deprecated": {"replacement": ..., "since": ..., "remove_in": ...}`` to its entry in ``surface.json``
   (``scripts/api_surface.py`` compares it with :data:`REGISTRY`);
3. name it under ``### Deprecated`` in the CHANGELOG.

Every use warns with :class:`DeprecationWarning` and a text that names the replacement and the version of removal:
``tabrion.x.old is deprecated since 0.2 and will be removed in 0.4; use tabrion.x.new instead``. S-3 fixes the window:
the removal is at the earliest in the minor version after next (``0.2`` -> ``0.4``), and in a new major version from
1.0 on. Standard library only, so every module of layer A may import it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import functools
import inspect
import re
from typing import Any, ParamSpec, TypeVar
import warnings

__all__: list[str] = []

P = ParamSpec("P")
R = TypeVar("R")

_VERSION = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class Deprecation:
    """One deprecated public name: its dotted path, the replacement, the deprecating and the removing version."""

    path: str
    replacement: str
    since: str
    remove_in: str

    @property
    def message(self) -> str:
        return (
            f"{self.path} is deprecated since {self.since} and will be removed in {self.remove_in}; "
            f"use {self.replacement} instead"
        )

    def as_surface(self) -> dict[str, str]:
        """The ``deprecated`` value of the name's ``surface.json`` entry."""
        return {"replacement": self.replacement, "since": self.since, "remove_in": self.remove_in}


REGISTRY: dict[str, Deprecation] = {}
"""Every deprecation registered at import time, by public path. ``scripts/api_surface.py`` reads it."""


def parse_version(version: str) -> tuple[int, int]:
    """``"0.2"`` -> ``(0, 2)``. Exactly MAJOR.MINOR: the window counts minor versions, never patches."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not a MAJOR.MINOR version: {version!r}")
    return int(match.group(1)), int(match.group(2))


def earliest_removal(since: str) -> tuple[int, int]:
    """S-3: the minor version after next; from 1.0 on the next major version (normal SemVer, S-1).

    Versions are compared in order, so ``1.0`` passes for every ``0.x``; whether a release lies between the
    deprecation and 1.0, and the eight weeks of S-3, are checks of the release checklist.
    """
    major, minor = parse_version(since)
    if major == 0:
        return 0, minor + 2
    return major + 1, 0


def register(path: str, *, replacement: str, since: str, remove_in: str) -> Deprecation:
    """Record a deprecation; raises ``ValueError`` for a window shorter than S-3.

    Registering the same deprecation again (``importlib.reload``, a module executed twice) returns the recorded one;
    a different deprecation of a registered path raises ``ValueError`` (review L-4).
    """
    if not path.startswith("tabrion.") or not replacement:
        raise ValueError(f"deprecation of {path!r}: needs a public tabrion path and a replacement")
    if parse_version(remove_in) < earliest_removal(since):
        raise ValueError(
            f"deprecation of {path}: removal in {remove_in} is too early; S-3 allows "
            f"{'.'.join(map(str, earliest_removal(since)))} at the earliest"
        )
    entry = Deprecation(path, replacement, since, remove_in)
    if path in REGISTRY:
        if REGISTRY[path] == entry:
            return REGISTRY[path]
        raise ValueError(f"deprecation of {path}: registered twice, differently")
    REGISTRY[path] = entry
    return entry


def warn(entry: Deprecation, *, stacklevel: int = 2) -> None:
    """Emit the warning for ``entry``; ``stacklevel`` counts from the caller of :func:`warn`, as in ``warnings``."""
    warnings.warn(entry.message, DeprecationWarning, stacklevel=stacklevel + 1)


def deprecated_callable(
    path: str, *, replacement: str, since: str, remove_in: str
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: the function warns on every call, pointing at the caller's line.

    ``functools.wraps`` keeps the name, the docstring and ``__wrapped__``, so ``inspect.signature`` (and with it the
    signature in ``surface.json``) stays the same. A class is refused with ``TypeError``: wrapping it would turn it
    into a function (``isinstance``, subclassing and ``dataclasses.fields`` would break); deprecate a moved class
    with :func:`module_getattr`, or warn in its ``__init__`` (review L-3). For an ``async def`` function the warning
    comes at the call; from Python 3.12 on the wrapper is marked as a coroutine function
    (``inspect.markcoroutinefunction``), on 3.11 ``inspect.iscoroutinefunction`` reports ``False`` for it.
    """
    entry = register(path, replacement=replacement, since=since, remove_in=remove_in)

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.isclass(fn):
            raise TypeError(
                f"deprecation of {path}: deprecated_callable wraps functions only; "
                "use module_getattr for a moved class, or warn in its __init__"
            )

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            warn(entry)
            return fn(*args, **kwargs)

        mark = getattr(inspect, "markcoroutinefunction", None)  # Python 3.12 and later
        if mark is not None and inspect.iscoroutinefunction(fn):
            mark(wrapper)
        return wrapper

    return decorate


def module_getattr(
    module_name: str,
    moved: Mapping[str, tuple[Deprecation, Callable[[], object]]],
    fallback: Callable[[str], Any] | None = None,
) -> Callable[[str], Any]:
    """A PEP 562 ``__getattr__`` for ``module_name``: a name in ``moved`` warns and returns its target.

    ``moved`` maps the old attribute name to its :class:`Deprecation` (from :func:`register`) and a function that
    loads the replacement object (lazily, so an optional extra is imported only on use). Any other name goes to
    ``fallback`` (the module's existing ``__getattr__``) or raises ``AttributeError``.
    """

    def __getattr__(name: str) -> Any:
        if name in moved:
            entry, load = moved[name]
            warn(entry)
            return load()
        if fallback is not None:
            return fallback(name)
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")

    return __getattr__
