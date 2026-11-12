"""QA-8 review M-1: the build backend is pinned exactly, in ``pyproject.toml`` and with hashes in
``build-constraints.txt``, and both pins agree."""

from __future__ import annotations

import re
import tomllib

from tests.conftest import ROOT

_LINE = re.compile(r"^(?P<name>[a-z0-9-]+)==(?P<version>\S+) --hash=sha256:[0-9a-f]{64}$")


def _constraints() -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (ROOT / "build-constraints.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        assert match is not None, f"not an exact pin with a SHA-256 hash: {line!r}"
        found[match["name"]] = match["version"]
    return found


def test_backend_is_pinned_exactly_and_hashed() -> None:
    build_system = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]
    assert build_system["build-backend"] == "hatchling.build"
    (requirement,) = build_system["requires"]
    name, separator, version = requirement.partition("==")
    assert separator == "==", f"build-system requires must be an exact pin, not {requirement!r}"
    constraints = _constraints()
    assert constraints.get(name) == version
    # hatchling's own dependencies are pinned too, or PIP_REQUIRE_HASHES would refuse the build.
    assert {"packaging", "pathspec", "pluggy", "trove-classifiers"} <= set(constraints)
