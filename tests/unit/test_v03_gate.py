"""The 0.3 gate of TAB-XW-6 (ADR 0015 Consequences, as G-T1 condition C-6 for 0.2; ``tabrion._since``).

``Transaction.execute_checked``, ``tabrion.errors.RowCountMismatch`` and ``tabrion.rowedit`` are unimportable while the
package version is below 0.3 and public from 0.3 on, with no flag or setting in between. Both states are proven
whatever the repository's own version is: in this process by setting ``tabrion._version.__version__`` (the module
import gate and :func:`tabrion._since.released`), and in fresh interpreters on copies of the package whose
``_version.py`` says 0.2 or 0.3 (the class and the error, which are decided at import time), including the API
surface check of ``scripts/api_surface.py`` at 0.3.
"""

from __future__ import annotations

from collections.abc import Iterator
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import tabrion._since
import tabrion._version
from tests.conftest import ROOT, SRC

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")  # the surface check imports every public module

VERSION_LINE = f'__version__ = "{tabrion._version.__version__}"'


@pytest.fixture
def fresh_rowedit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``tabrion.rowedit`` is imported anew by the test and forgotten afterwards."""
    monkeypatch.delitem(sys.modules, "tabrion.rowedit", raising=False)
    yield
    sys.modules.pop("tabrion.rowedit", None)
    if hasattr(tabrion, "rowedit"):
        delattr(tabrion, "rowedit")


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.2.2.dev0", False), ("0.2.9", False), ("0.3.0.dev0", True), ("0.3.0", True), ("0.4.1", True), ("1.0.0", True)],
)
def test_released_reads_the_version_when_called(monkeypatch: pytest.MonkeyPatch, version: str, expected: bool) -> None:
    monkeypatch.setattr(tabrion._version, "__version__", version)
    assert tabrion._since.released("0.3") is expected


def test_released_refuses_a_text_that_is_no_version() -> None:
    with pytest.raises(ValueError, match="not a version"):
        tabrion._since.released("next")


@pytest.mark.usefixtures("fresh_rowedit")
def test_rowedit_is_not_importable_below_0_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tabrion._version, "__version__", "0.2.2.dev0")
    with pytest.raises(ImportError, match=r"planned for Tabrion 0\.3") as caught:
        importlib.import_module("tabrion.rowedit")
    assert caught.value.name == "tabrion.rowedit"
    assert "tabrion.rowedit" not in sys.modules


@pytest.mark.usefixtures("fresh_rowedit")
def test_rowedit_is_importable_from_0_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tabrion._version, "__version__", "0.3.0.dev0")
    module = importlib.import_module("tabrion.rowedit")
    staged = importlib.import_module("tabrion._sa._rowedit")
    assert module.__all__ == ["delete_row", "insert_row", "update_row"]
    for name in module.__all__:
        assert getattr(module, name) is getattr(staged, name)


def _package(tmp_path: Path, version: str) -> Path:
    src = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", src / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    path = src / "tabrion" / "_version.py"
    text = path.read_text(encoding="utf-8")
    assert VERSION_LINE in text
    path.write_text(text.replace(VERSION_LINE, f'__version__ = "{version}"'), encoding="utf-8")
    return src


def _python(src: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(src)
    return subprocess.run(
        [sys.executable, *args], env=env, capture_output=True, text=True, timeout=120, check=False, cwd=src
    )


PROBE = """
import sys
import tabrion, tabrion.errors, tabrion._sa._rowedit, tabrion._sa._v03
print(tabrion.__version__)
print(hasattr(tabrion.Transaction, "execute_checked"))
print(hasattr(tabrion.errors, "RowCountMismatch"), "RowCountMismatch" in tabrion.errors.__all__)
try:
    import tabrion.rowedit as rowedit
except ImportError:
    print("rowedit: ImportError")
else:
    print("rowedit:", rowedit.update_row is tabrion._sa._rowedit.update_row)
if hasattr(tabrion.Transaction, "execute_checked"):
    import sqlalchemy
    assert tabrion.errors.RowCountMismatch is tabrion.errors._RowCountMismatch
    meta = sqlalchemy.MetaData()
    t = sqlalchemy.Table("t", meta, sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
                         sqlalchemy.Column("v", sqlalchemy.String))
    with tabrion.Database.from_url("sqlite://") as db:
        with db.transaction() as tx:
            meta.create_all(tx.connection)
            tx.execute_checked(rowedit.insert_row(t, {"id": 1, "v": "a"}), expect_rowcount=1)
        try:
            with db.transaction() as tx:
                tx.execute_checked(rowedit.update_row(t, {"id": 1}, {"v": "b"}, {"v": "c"}), expect_rowcount=1)
        except tabrion.errors.RowCountMismatch as exc:
            print("conflict:", exc.code, dict(exc.details))
"""


def test_below_0_3_nothing_is_public(tmp_path: Path) -> None:
    result = _python(_package(tmp_path, "0.2.9"), "-c", PROBE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["0.2.9", "False", "False False", "rowedit: ImportError"]


def test_from_0_3_everything_is_public(tmp_path: Path) -> None:
    result = _python(_package(tmp_path, "0.3.0.dev0"), "-c", PROBE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "0.3.0.dev0",
        "True",
        "True True",
        "rowedit: True",
        "conflict: transaction.rowcount_mismatch {'expected': 1, 'actual': 0}",
    ]


@pytest.mark.parametrize("version", ["0.2.9", "0.3.0.dev0"])
def test_surface_check_is_consistent_in_both_states(tmp_path: Path, version: str) -> None:
    """``scripts/api_surface.py``: below 0.3 the names are staged and not importable; at 0.3 they are public with
    exactly the frozen signatures (the 0.3 switch only drops ``staged``, as TAB-042 did for 0.2)."""
    script = ROOT / "scripts" / "api_surface.py"
    result = _python(_package(tmp_path, version), str(script), "--surface", str(ROOT / "docs" / "api" / "surface.json"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"package {version[:3]}: consistent" in result.stdout
    assert ("(0 staged for a later minor)" in result.stdout) is version.startswith("0.3")
