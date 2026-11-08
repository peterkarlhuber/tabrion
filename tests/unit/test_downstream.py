"""``scripts/downstream.py`` (TAB-022b): HEAD replaces every Tabrion requirement of a consumer (review M-1 of #19).

The packages are wheels built by hand in ``tmp_path`` and installed with ``--offline --no-index``, so the test
needs no network and no build backend. The stand-in for HEAD is a ``tabrion`` wheel with HEAD's pre-release
version, which satisfies neither the range nor the Git pin of the consumers.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import venv
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHA = "0123456789abcdef0123456789abcdef01234567"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("downstream", ROOT / "scripts" / "downstream.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["downstream"] = module
    spec.loader.exec_module(module)
    return module


downstream = _script()


def _uv() -> Path:
    try:
        import uv
    except ImportError:
        pytest.skip("uv is not installed (the dev extra brings it)")
    return Path(uv.find_uv_bin())


def _wheel(directory: Path, name: str, version: str, requires: list[str], marker: str = "") -> Path:
    """Write a minimal pure-Python wheel ``name`` with one module that holds ``marker``."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"MARKER = {marker!r}\n",
        f"{dist}/METADATA": "Metadata-Version: 2.1\n"
        f"Name: {name}\nVersion: {version}\n" + "".join(f"Requires-Dist: {r}\n" for r in requires),
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: hand\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\n".join(record) + "\n"
    target = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return target


@pytest.fixture
def index(tmp_path: Path) -> Path:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "tabrion", "0.1.0", [], marker="released")  # what the range would pick from an index
    _wheel(wheels, "consumer_range", "1.0", ["tabrion>=0.1,<0.2"])
    _wheel(wheels, "consumer_pin", "1.0", [f"tabrion @ git+https://github.com/peterkarlhuber/tabrion@{SHA}"])
    _wheel(wheels, "consumer_transitive", "1.0", ["consumer_pin"])
    return wheels


@pytest.mark.parametrize("consumer", ["consumer_range", "consumer_pin", "consumer_transitive"])
def test_head_replaces_the_consumers_tabrion_requirement(consumer: str, index: Path, tmp_path: Path) -> None:
    uv = _uv()
    head_dir = tmp_path / "head"
    head_dir.mkdir()
    head = _wheel(head_dir, "tabrion", "0.1.0a2.dev0", [], marker="HEAD")
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    python = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    offline = ("--offline", "--no-index", "--find-links", str(index))

    downstream.install_with_head(uv, python, [consumer], head=f"tabrion @ {head.as_uri()}", extra=offline)

    marker = subprocess.run(
        [str(python), "-I", "-c", "import tabrion, " + consumer + "; print(tabrion.MARKER)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert marker.stdout.strip() == "HEAD"


def test_a_tabrion_that_is_not_head_is_a_setup_failure(
    index: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uv = _uv()
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    python = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    head = _wheel(tmp_path, "tabrion", "0.1.0a2.dev0", [], marker="HEAD")
    offline = ("--offline", "--no-index", "--find-links", str(index))
    monkeypatch.setattr(downstream, "_INSTALLED_URL", "print('file:///somewhere/else')")
    with pytest.raises(downstream.SetupFailed, match="tabrion is not HEAD"):
        downstream.install_with_head(uv, python, ["consumer_range"], head=f"tabrion @ {head.as_uri()}", extra=offline)


def test_report_cells_escape_pipes_and_line_breaks() -> None:
    outcome = downstream.Outcome("axentra", "a" * 40, False, "1 failed | 2 passed\nnext", 60.0)
    table = downstream.report([outcome], "b" * 40)
    row = table.splitlines()[-1]
    assert row == "| axentra | `aaaaaaaaaaaa` | **failed** | 1 failed \\| 2 passed next | 1.0 |"


def test_a_database_error_is_a_setup_failure_without_its_message() -> None:
    url = "postgresql://nobody:secret-canary@127.0.0.1:1/none?connect_timeout=1"
    with pytest.raises(downstream.SetupFailed) as caught:
        downstream._database(url, "t_000000_ds_x", drop=False)
    assert "create database failed (OperationalError" in str(caught.value)
    assert "secret-canary" not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)
