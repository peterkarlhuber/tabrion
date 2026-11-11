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


def _wheel(
    directory: Path, name: str, version: str, requires: list[str], marker: str = "", extras: tuple[str, ...] = ()
) -> Path:
    """Write a minimal pure-Python wheel ``name`` with one module that holds ``marker``."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"MARKER = {marker!r}\n",
        f"{dist}/METADATA": "Metadata-Version: 2.1\n"
        f"Name: {name}\nVersion: {version}\n"
        + "".join(f"Provides-Extra: {e}\n" for e in extras)
        + "".join(f"Requires-Dist: {r}\n" for r in requires),
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


def _head(directory: Path, marker: str = "HEAD") -> Path:
    """HEAD's stand-in: a pre-release ``tabrion`` whose extras ``sqlalchemy`` and ``all`` bring ``sqlalchemy``."""
    directory.mkdir(exist_ok=True)
    requires = ['sqlalchemy; extra == "sqlalchemy"', 'sqlalchemy; extra == "all"']
    return _wheel(directory, "tabrion", "0.1.0a2.dev0", requires, marker=marker, extras=("sqlalchemy", "all"))


def _venv(tmp_path: Path) -> Path:
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    python: Path = downstream.venv_python(tmp_path / "venv")
    return python


def _marker(python: Path, *modules: str) -> str:
    imports = ", ".join(("tabrion", *modules))
    code = f"import {imports}; print(tabrion.MARKER)"
    return subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def index(tmp_path: Path) -> Path:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "tabrion", "0.1.0", [], marker="released")  # what the range would pick from an index
    _wheel(wheels, "consumer_range", "1.0", ["tabrion>=0.1,<0.2"])
    _wheel(wheels, "consumer_pin", "1.0", [f"tabrion @ git+https://github.com/peterkarlhuber/tabrion@{SHA}"])
    _wheel(wheels, "consumer_transitive", "1.0", ["consumer_pin"])
    _wheel(
        wheels, "consumer_extra", "1.0", [f"tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@{SHA}"]
    )
    _wheel(wheels, "sqlalchemy", "2.0.99", [], marker="stand-in")
    return wheels


@pytest.mark.parametrize("consumer", ["consumer_range", "consumer_pin", "consumer_transitive", "consumer_extra"])
def test_head_replaces_the_consumers_tabrion_requirement(consumer: str, index: Path, tmp_path: Path) -> None:
    """Every form of Tabrion requirement ends with HEAD; ``consumer_extra`` keeps the extra it asked for, SQLAlchemy
    (the override is ``tabrion[all]``, review N-1 of #19)."""
    uv = _uv()
    head = _head(tmp_path / "head")
    python = _venv(tmp_path)
    offline = ("--offline", "--no-index", "--find-links", str(index))

    downstream.install_with_head(uv, python, [consumer], head=downstream.head_requirement(head.as_uri()), extra=offline)

    assert _marker(python, consumer, *(["sqlalchemy"] if consumer == "consumer_extra" else [])) == "HEAD"


# A PEP 517 backend in HEAD's own tree, so a directory source builds offline: it packs ``tabrion/`` as written.
_BACKEND = """
import base64, hashlib, pathlib, zipfile

NAME, VERSION = "tabrion", "0.1.0a2.dev0"
META = f"Metadata-Version: 2.1\\nName: {NAME}\\nVersion: {VERSION}\\nProvides-Extra: all\\n"


def get_requires_for_build_wheel(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    dist = f"{NAME}-{VERSION}.dist-info"
    files = {p.as_posix(): p.read_bytes() for p in pathlib.Path(NAME).rglob("*.py")}
    files[f"{dist}/METADATA"] = META.encode()
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"
    record = [
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}"
        for path, data in files.items()
    ]
    files[f"{dist}/RECORD"] = ("\\n".join([*record, f"{dist}/RECORD,,"]) + "\\n").encode()
    name = f"{NAME}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return name
"""


def test_head_is_rebuilt_when_only_its_code_changed(index: Path, tmp_path: Path) -> None:
    """HEAD is a directory, as the checkout is. A second run after a change to its code only (same version, same
    ``pyproject.toml``) installs the new code, not the build already installed or cached (Info of the delta
    re-review of #19). This pins the behavior, not the flag: uv 0.11 already reinstalls a local directory given on
    the command line, so ``--reinstall-package tabrion`` is a guard for a uv that would not, and dropping it alone
    keeps this test green."""
    uv = _uv()
    head = tmp_path / "head"
    (head / "tabrion").mkdir(parents=True)
    (head / "backend.py").write_text(_BACKEND, encoding="utf-8")
    (head / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n', encoding="utf-8"
    )
    module = head / "tabrion" / "__init__.py"
    python = _venv(tmp_path)
    offline = ("--offline", "--no-index", "--find-links", str(index), "--cache-dir", str(tmp_path / "cache"))
    for marker in ("before", "HEAD"):
        module.write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
        downstream.install_with_head(
            uv, python, ["consumer_range"], head=downstream.head_requirement(head.as_uri()), extra=offline
        )
        assert _marker(python) == marker


def test_a_tabrion_that_is_not_head_is_a_setup_failure(
    index: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uv = _uv()
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    python = downstream.venv_python(tmp_path / "venv")
    head = _wheel(tmp_path, "tabrion", "0.1.0a2.dev0", [], marker="HEAD")
    offline = ("--offline", "--no-index", "--find-links", str(index))
    monkeypatch.setattr(downstream, "_INSTALLED_URL", "print('file:///somewhere/else')")
    with pytest.raises(downstream.SetupFailed, match="tabrion is not HEAD"):
        downstream.install_with_head(uv, python, ["consumer_range"], head=f"tabrion @ {head.as_uri()}", extra=offline)


def test_report_cells_escape_pipes_and_line_breaks() -> None:
    outcome = downstream.Outcome("axentra", "a" * 40, False, "1 failed | 2 passed\nnext", 60.0, note="a | b")
    table = downstream.report([outcome], "b" * 40)
    row = table.splitlines()[-1]
    assert row == "| axentra | `aaaaaaaaaaaa` | **failed** | 1 failed \\| 2 passed next | 1.0 | a \\| b |"


def test_venv_python_is_the_interpreter_file(tmp_path: Path) -> None:
    """Issue #18: the path handed to ``uv pip install --python`` is the interpreter file itself, ``python.exe`` on
    Windows. ``Scripts\\python`` without the extension starts through ``subprocess`` but is no file, and uv finds no
    environment there (exit 2)."""
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    python = downstream.venv_python(tmp_path / "venv")
    assert python.is_file()
    if sys.platform == "win32":
        assert python.parts[-2:] == ("Scripts", "python.exe")
        assert not python.with_suffix("").is_file()
    else:
        assert python.parts[-2:] == ("bin", "python")


def test_a_database_error_is_a_setup_failure_without_its_message() -> None:
    """The report names the psycopg error class, never its message. A connection to a closed local port is refused at
    once on Linux and macOS (``OperationalError``); Windows answers the refusal by retrying the connect until
    ``connect_timeout`` (1 s) runs out, so psycopg raises its subclass ``ConnectionTimeout`` there (issue #18)."""
    import psycopg

    url = "postgresql://nobody:secret-canary@127.0.0.1:1/none?connect_timeout=1"
    with pytest.raises(downstream.SetupFailed) as caught:
        downstream._database(url, "t_000000_ds_x", drop=False)
    cause = caught.value.__cause__
    assert isinstance(cause, psycopg.OperationalError)
    allowed = {"OperationalError", "ConnectionTimeout"} if sys.platform == "win32" else {"OperationalError"}
    assert type(cause).__name__ in allowed
    assert str(caught.value) == f"create database failed ({type(cause).__name__}, SQLSTATE -)"
    assert "secret-canary" not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)


def test_a_failed_drop_keeps_the_test_result_and_adds_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Info of the delta re-review of #19: the consumer's result stands, the database left behind is named."""
    calls: list[tuple[str, bool]] = []

    def database(admin_url: str, name: str, *, drop: bool) -> str:
        calls.append((name, drop))
        if drop:
            raise downstream.SetupFailed("drop database failed (OperationalError, SQLSTATE 08006)")
        return "dbname=" + name

    monkeypatch.setattr(downstream, "_database", database)
    passed = downstream.Outcome("axentra", "c" * 40, True, "100 passed", 1.0)
    outcome = downstream.with_database("admin", "t_000000_ds_axentra", lambda url: passed)
    assert calls == [("t_000000_ds_axentra", False), ("t_000000_ds_axentra", True)]
    assert (outcome.passed, outcome.detail) == (True, "100 passed")
    assert outcome.note == "drop database failed (OperationalError, SQLSTATE 08006); t_000000_ds_axentra left behind"


def test_a_failed_uv_install_is_a_failed_row_per_consumer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Info of the delta re-review of #19: without ``uv`` every selected consumer gets a row; nothing is cloned."""

    def no_uv(work: Path) -> Path:
        raise subprocess.CalledProcessError(1, ["pip", "install", "uv"])

    def no_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("no consumer may run without uv")

    monkeypatch.setattr(downstream, "find_uv", no_uv)
    monkeypatch.setattr(downstream, "run_consumer", no_run)
    assert downstream.main(["--admin-url", "dbname=unused", "--consumer", "axentra", "--consumer", "orkexa"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| ") and "Consumer" not in line]
    assert [row.split(" | ")[0] for row in rows] == ["| axentra", "| orkexa"]
    assert all("setup failed (installing uv" in row for row in rows)
