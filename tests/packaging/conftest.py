"""Fixtures for the packaging tests (marker ``packaging``): a built wheel and sdist, shared per session."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from tests.conftest import ROOT


@pytest.fixture(scope="session")
def dist_dir() -> Iterator[Path]:
    """``TABRION_DIST`` (built by CI) or a fresh ``python -m build`` into a temporary directory."""
    given = os.environ.get("TABRION_DIST")
    if given:
        yield Path(given).resolve()
        return
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "build", "--outdir", tmp, str(ROOT)], check=True, capture_output=True)
        yield Path(tmp)


@pytest.fixture(scope="session")
def wheel(dist_dir: Path) -> Path:
    (path,) = dist_dir.glob("tabrion-*.whl")
    return path


@pytest.fixture(scope="session")
def sdist(dist_dir: Path) -> Path:
    (path,) = dist_dir.glob("tabrion-*.tar.gz")
    return path


def make_venv(path: Path) -> Path:
    """A fresh virtual environment; returns its Python."""
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True, capture_output=True)
    python = path / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    return python


def pip(python: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python), "-m", "pip", "--disable-pip-version-check", "--no-input", *args],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
