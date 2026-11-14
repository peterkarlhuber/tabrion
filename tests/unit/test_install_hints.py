"""DC-TB-1 (TODO TB-132): no text Tabrion ships or documents tells anyone to resolve the bare name from an index.

Tabrion and Tabrion Studio are not on PyPI, and by owner decision (2026-11-13) the names are not registered there
either. A bare ``pip install tabrion`` or ``pip install "tabrion[...]"`` would therefore install whatever a third party
registers under the name (dependency confusion). Every install hint and example names the Git-pin form
``tabrion[...] @ git+https://github.com/peterkarlhuber/tabrion@<commit>`` instead; SECURITY.md lists the residual risk
and is the one place that names the bare forms, as the exposed case."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: What is scanned: the package, the documentation site and the top-level guides a user copies commands from.
SCANNED = ("src", "docs", "examples", "README.md", "CONTRIBUTING.md")
SUFFIXES = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".json", ".sh"}

#: ``pip install`` (or ``uv pip install``, any options) of ``tabrion`` or ``tabrion-studio``, with or without quotes
#: and extras, that is not followed by `` @ `` (a direct URL). The extras group is possessive, so a pinned
#: ``tabrion[x] @ ...`` cannot match by backtracking out of the extras.
BARE_INSTALL = re.compile(
    r"""pip3?\s+install\s+(?:-{1,2}[\w-]+(?:=\S+)?\s+)*?["']?tabrion(?:-studio)?(?:\[[^\]\n]*\])?+(?!\s*@)(?![\w.-])"""
)
#: A Git URL of this repository must name a full commit (or a documented placeholder), never a moving ref.
GIT_URL = re.compile(r"git\+https://github\.com/peterkarlhuber/tabrion(?:-studio)?(?:\.git)?(?P<ref>@[^\s\"'`)]*)?")
PLACEHOLDERS = ("@<commit-sha>", "@<full")


def _files() -> list[Path]:
    out: list[Path] = []
    for name in SCANNED:
        path = ROOT / name
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out.extend(p for p in sorted(path.rglob("*")) if p.is_file() and p.suffix in SUFFIXES)
    return out


def _lines() -> list[tuple[str, int, str]]:
    if not (ROOT / "docs").is_dir():
        pytest.skip("needs a repository checkout")
    out: list[tuple[str, int, str]] = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        out.extend((str(path.relative_to(ROOT)), n, line) for n, line in enumerate(text.splitlines(), 1))
    return out


@pytest.mark.parametrize(
    "text",
    [
        "pip install tabrion",
        "pip install tabrion-studio",
        'pip install "tabrion"',
        'pip install "tabrion[sqlalchemy]"',
        "pip install 'tabrion[postgres,sqlalchemy]'",
        "pip install tabrion[testing]",
        "pip install -U tabrion",
        "pip install --upgrade tabrion",
        "uv pip install tabrion",
        "pip3 install tabrion",
        'ImportError: x needs the extra "postgres": pip install "tabrion[postgres]"',
    ],
)
def test_the_pattern_finds_a_bare_install(text: str) -> None:
    assert BARE_INSTALL.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        'pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"',
        'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"',
        "pip install 'tabrion[postgres,sqlalchemy]@git+https://github.com/peterkarlhuber/tabrion@0123abc'",
        "pip install tabrion-other",
        "pip install tabrionx",
        "pip install -r requirements/tabrion.txt",
        'pip install -e ".[dev]"',
    ],
)
def test_the_pattern_keeps_a_pinned_install(text: str) -> None:
    assert not BARE_INSTALL.search(text), text


def test_no_bare_install_command_in_the_package_or_the_docs() -> None:
    found = [f"{path}:{n}: {line.strip()}" for path, n, line in _lines() if BARE_INSTALL.search(line)]
    assert not found, "a bare install resolves the name from an index (DC-TB-1):\n" + "\n".join(found)


def test_every_git_url_of_this_repository_names_a_commit() -> None:
    """README and docs pin a full commit (or show the documented placeholder), never ``master`` or no ref at all."""
    found: list[str] = []
    for path, n, line in _lines():
        for match in GIT_URL.finditer(line):
            ref = match["ref"] or ""
            if re.fullmatch(r"@[0-9a-f]{40}", ref) or ref.startswith(PLACEHOLDERS):
                continue
            found.append(f"{path}:{n}: {match[0]}")
    assert not found, "a Git URL without a full commit (DC-TB-1):\n" + "\n".join(found)


def test_security_policy_records_the_unregistered_name() -> None:
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8") if (ROOT / "SECURITY.md").is_file() else ""
    if not policy:
        pytest.skip("needs a repository checkout")
    assert "**Supply chain: unregistered PyPI name.**" in policy
    for needed in ("`tabrion-studio`", "--require-hashes", "Dependabot", "pip-audit", "G-R"):
        assert needed in policy, needed
