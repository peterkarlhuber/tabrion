"""Internal version gate for names of a later minor version (ADR 0015 Consequences; as G-T1 condition C-6 for 0.2).

Not public: nothing here is in an ``__all__``, and ``docs/api/surface.json`` has no entry for it. A name planned for a
later minor version (``"since": "0.3"`` in ``surface.json``) is implemented ahead of that version in a private module
(its ``staged`` location) and stays unimportable from its public path while the package version is below it:

* a method joins its public class through a private mixin only when :func:`released` says so (``Transaction`` and the
  0.3 mixin of ``tabrion._sa._v03``);
* a module-level name is bound in its public module only then (``tabrion.errors.RowCountMismatch``);
* a new public module raises ``ImportError`` on import before then (``tabrion.rowedit``).

The decision reads ``tabrion._version.__version__``, so the version bump to 0.3 is what makes the names public; there
is no flag, setting or environment variable that unlocks them (ADR 0015 section 7). ``scripts/api_surface.py`` checks
both states: a name of a later minor that is importable fails, and so does a name of the package's minor that is not.
Standard library only, so every module of layer A may import it.
"""

from __future__ import annotations

import re

from tabrion import _version

__all__: list[str] = []

_VERSION = re.compile(r"(\d+)\.(\d+)")


def minor(version: str) -> tuple[int, int]:
    """``(major, minor)`` of a version text such as ``"0.2.2.dev0"`` or ``"0.3"``."""
    match = _VERSION.match(version)
    if match is None:
        raise ValueError(f"not a version: {version!r}")
    return int(match.group(1)), int(match.group(2))


def released(since: str) -> bool:
    """Whether the package version has reached the minor version ``since`` (``"0.3"``); a ``.dev`` version of that
    minor counts, as ``scripts/api_surface.py`` counts it. Read when called, never cached."""
    return minor(_version.__version__) >= minor(since)
