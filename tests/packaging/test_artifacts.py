"""Job "build and install (fresh virtual environments)": wheel and sdist content, metadata (TAB-T-180), install."""

from __future__ import annotations

import email.parser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

from tests.conftest import ROOT
from tests.packaging.conftest import make_venv, pip

pytestmark = pytest.mark.packaging

SMOKE = Path(__file__).with_name("smoke.py")


def _metadata(wheel: Path) -> email.message.Message:
    with zipfile.ZipFile(wheel) as zf:
        (name,) = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        return email.parser.Parser().parsestr(zf.read(name).decode("utf-8"))


def test_core_wheel_requires_nothing(wheel: Path) -> None:
    """TAB-T-180: every Requires-Dist of the wheel belongs to an extra."""
    meta = _metadata(wheel)
    requires = meta.get_all("Requires-Dist") or []
    assert requires
    assert all("extra ==" in r for r in requires), requires
    extras = set(meta.get_all("Provides-Extra") or [])
    assert extras == {"postgres", "sqlalchemy", "migrations", "testing", "all", "dev"}
    assert meta["Requires-Python"] == ">=3.11"
    assert meta["License-Expression"] == "MIT"


def test_wheel_contains_only_the_package(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert "tabrion/py.typed" in names
    assert "tabrion/__init__.py" in names
    top = {n.split("/")[0] for n in names}
    assert {t for t in top if not t.endswith(".dist-info")} == {"tabrion"}, top


def test_sdist_contains_the_sources_and_tests(sdist: Path) -> None:
    with tarfile.open(sdist) as tf:
        names = {n.split("/", 1)[1] for n in tf.getnames() if "/" in n}
    assert {"pyproject.toml", "LICENSE", "README.md", "src/tabrion/__init__.py", "tests/conftest.py"} <= names


def test_install_from_wheel_in_a_fresh_venv(wheel: Path, tmp_path: Path) -> None:
    python = make_venv(tmp_path / "venv")
    result = pip(python, "install", str(wheel))
    assert result.returncode == 0, result.stderr
    assert pip(python, "check").returncode == 0
    smoke = subprocess.run([str(python), "-I", str(SMOKE), ""], capture_output=True, text=True, check=False)
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr


def _build_wheel(outdir: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), str(ROOT)], check=True, capture_output=True
    )
    (path,) = outdir.glob("tabrion-*.whl")
    return path


def test_two_builds_give_the_same_wheel_hash(wheel: Path, tmp_path: Path) -> None:
    """QA-8: the wheel is reproducible: two fresh builds and the job's own build have the same SHA-256."""
    first = _build_wheel(tmp_path / "first")
    second = _build_wheel(tmp_path / "second")
    digests = {_sha256(p) for p in (first, second, wheel)}
    assert len(digests) == 1, digests


def test_sbom_of_the_wheel(wheel: Path, tmp_path: Path) -> None:
    """QA-8: ``scripts/sbom.py`` describes the built wheel (CycloneDX 1.6, its hash, every declared dependency)."""
    out = tmp_path / "sbom.cdx.json"
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "sbom.py"), str(wheel), "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["specVersion"] == "1.6"
    assert doc["metadata"]["component"]["hashes"][0]["content"] == _sha256(wheel)
    names = {c["name"] for c in doc["components"]}
    assert {"psycopg", "sqlalchemy", "alembic", "pytest"} <= names
    assert all(c["scope"] == "optional" for c in doc["components"])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
