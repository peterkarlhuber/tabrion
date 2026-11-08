"""Job "extras matrix" (TAB-005, QA-5, 05 section 2.2 and 07 K14): each extras combination in a fresh venv.

The plan names seven combinations twice, differently (05 section 2.2 and 07 section 4.1 K14); this matrix runs the
union of both lists, eight combinations. Each one installs from the built wheel, passes ``pip check`` and the import
smoke test ``smoke.py``.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.packaging.conftest import make_venv, pip

pytestmark = pytest.mark.packaging

SMOKE = Path(__file__).with_name("smoke.py")

COMBINATIONS = [
    "",  # the core, no extra (05, 07)
    "postgres",  # 05, 07
    "sqlalchemy",  # 05, 07
    "migrations",  # 05, 07
    "testing",  # 07
    "postgres,sqlalchemy",  # 05, 07
    "postgres,migrations,testing",  # 05
    "all",  # 05, 07
]


@pytest.mark.parametrize("extras", COMBINATIONS, ids=lambda e: e or "core")
def test_combination_installs_and_imports(extras: str, wheel: Path, tmp_path: Path) -> None:
    python = make_venv(tmp_path / "venv")
    target = f"{wheel}[{extras}]" if extras else str(wheel)
    install = pip(python, "install", target)
    assert install.returncode == 0, install.stdout + install.stderr
    check = pip(python, "check")
    assert check.returncode == 0, check.stdout + check.stderr
    smoke = subprocess.run([str(python), "-I", str(SMOKE), extras], capture_output=True, text=True, check=False)
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert "ok" in smoke.stdout
