"""TAB-T-181 (TM-10): every GitHub Action is pinned by a full commit SHA and every container image by digest."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from tests.conftest import ROOT

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
IMAGE = re.compile(r"^\s*(?:-\s+)?image:\s*(\S+)(.*)$")
SHA_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")

pytestmark = pytest.mark.security


def _lines(path: Path) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)]


def test_there_are_workflows() -> None:
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "ci-ok.yml", "nightly.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_commit_sha(path: Path) -> None:
    for number, line in _lines(path):
        match = USES.match(line)
        if not match:
            continue
        ref, rest = match.group(1), match.group(2)
        if ref.startswith("./"):
            continue  # a reusable workflow of this repository
        assert SHA_PIN.match(ref), f"{path.name}:{number}: {ref} is not pinned by a 40-character commit SHA"
        assert re.search(r"#\s*v\d", rest), f"{path.name}:{number}: add the version as a comment (# vX.Y.Z)"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_images_are_pinned_by_digest(path: Path) -> None:
    for number, line in _lines(path):
        match = IMAGE.match(line)
        if not match:
            continue
        ref = match.group(1).strip("\"'")
        if ref == "${{":  # an expression such as ${{ matrix.image }}: its values are image: lines of the matrix
            assert "matrix.image" in line, f"{path.name}:{number}: only matrix.image may be an expression"
            continue
        assert DIGEST.search(ref), f"{path.name}:{number}: {ref} is not pinned by digest"


def test_the_checks_catch_a_tag(tmp_path: Path) -> None:
    assert not SHA_PIN.match("actions/checkout@v4")
    assert SHA_PIN.match("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
    assert not DIGEST.search("postgres:17")
