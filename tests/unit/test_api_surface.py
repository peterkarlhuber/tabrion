"""TAB-023 (G-T1 condition C-6): ``scripts/api_surface.py`` freezes every signature and keeps 0.2 names out of 0.1.

The positive control runs the check on the repository; the mutation probes change one thing in a copy of the package
and expect the check to fail and name it.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.conftest import ROOT, SRC

for _extra in ("sqlalchemy", "alembic"):
    pytest.importorskip(_extra)

SCRIPT = ROOT / "scripts" / "api_surface.py"
SURFACE = ROOT / "docs" / "api" / "surface.json"


def _run(src: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(src)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--surface", str(SURFACE), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


Edit = tuple[str, str, str]
"""(module path below src/tabrion, old text, new text)."""


def _mutated(tmp_path: Path, edits: list[Edit]) -> Path:
    src = tmp_path / "src"
    shutil.copytree(SRC / "tabrion", src / "tabrion", ignore=shutil.ignore_patterns("__pycache__"))
    for module, old, new in edits:
        path = src / "tabrion" / module
        text = path.read_text(encoding="utf-8")
        assert old in text, (module, old)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return src


def test_surface_is_consistent() -> None:
    result = _run(SRC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "consistent" in result.stdout


MUTATIONS: dict[str, tuple[list[Edit], str]] = {
    "changed default": (
        [("_sa/database.py", "timeout: float = 5.0, deep", "timeout: float = 6.0, deep")],
        "tabrion.Database.health",
    ),
    "keyword-only becomes positional": (
        [
            (
                "sqlite.py",
                "path: str | os.PathLike[str],\n    *,\n    profile",
                "path: str | os.PathLike[str],\n    profile",
            )
        ],
        "tabrion.sqlite.connect",
    ),
    "dataclass field default": (
        [("postgres/__init__.py", "statement_timeout_ms: int | None = 30_000", "statement_timeout_ms: int | None = 1")],
        "tabrion.postgres.SessionSettings",
    ),
    "0.2 name exported in 0.1": (
        [
            ("__init__.py", '    "Database",\n', '    "Database",\n    "Result",\n'),
            ("__init__.py", "_LAYER_B = {\n", '_LAYER_B = {\n    "Result": "tabrion._sa.result",\n'),
        ],
        "tabrion.Result: since 0.2",
    ),
    "0.2 name importable through __getattr__ only": (
        [("__init__.py", "_LAYER_B = {\n", '_LAYER_B = {\n    "Result": "tabrion._sa.result",\n')],
        "tabrion.Result: since 0.2, but it is importable from its public path",
    ),
    "0.2 mixin mixed in early": (
        [
            ("_sa/database.py", "class Database:", "class Database(_DatabaseV02):"),
            (
                "_sa/database.py",
                "\n\n\nclass Database(",
                "\n\nfrom tabrion._sa._v02 import _DatabaseV02  # noqa: E402\n\n\nclass Database(",
            ),
        ],
        "tabrion.Database.query: since 0.2",
    ),
    "staged 0.2 signature changed": (
        [("_sa/_v02.py", "batch_size: int = 500", "batch_size: int = 100")],
        "tabrion.Transaction.stream: staged signature changed",
    ),
    "deprecation without surface.json and CHANGELOG (TAB-051)": (
        [
            (
                "sql.py",
                "\ndef split_statements(",
                "\nfrom tabrion._deprecated import deprecated_callable  # noqa: E402\n\n\n"
                '@deprecated_callable("tabrion.sql.split_statements", replacement="x", since="0.1", remove_in="0.3")\n'
                "def split_statements(",
            )
        ],
        "tabrion.sql.split_statements: deprecation differs",
    ),
    "new public name": (
        [
            (
                "sql.py",
                '__all__ = ["split_statements"]',
                '__all__ = ["split_statements", "extra"]\n\n\ndef extra() -> None: ...',
            )
        ],
        "tabrion.sql.extra: new public name",
    ),
}


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_mutation_turns_red(tmp_path: Path, case: str) -> None:
    edits, message = MUTATIONS[case]
    result = _run(_mutated(tmp_path, edits))
    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr, result.stderr


# --- TB-022: the release check ``--no-stubs`` -------------------------------------------------------------------------


def test_no_stubs_on_the_repository() -> None:
    """Positive control: no name of the package's minor is a stub or only staged (TAB-040, G-T2)."""
    result = _run(SRC, "--no-stubs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "consistent, no stubs" in result.stdout


def _stub_body(tmp_path: Path, module: str, header: str, body: str) -> Path:
    """A copy of the package whose function starting with ``header`` has its whole body replaced by ``body``."""
    src = _mutated(tmp_path, [])
    path = src / "tabrion" / module
    tree_source = path.read_text(encoding="utf-8")
    tree = ast.parse(tree_source)
    lines = tree_source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and lines[node.lineno - 1].lstrip().startswith(header):
            first = node.body[0]
            docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            start = node.body[1].lineno if docstring else first.lineno
            indent = " " * node.body[-1].col_offset
            assert node.end_lineno is not None
            new = [*lines[: start - 1], f"{indent}{body}\n", *lines[node.end_lineno :]]
            path.write_text("".join(new), encoding="utf-8")
            return src
    raise AssertionError(header)


@pytest.mark.parametrize(
    ("module", "header", "body", "message"),
    [
        (
            "sql.py",
            "def split_statements(",
            'raise NotImplementedError("tabrion.sql.split_statements: implemented in TAB-011")',
            "tabrion.sql.split_statements: since 0.1, but it is a stub (its body only raises NotImplementedError)",
        ),
        (
            "_sa/database.py",
            "def health(",
            "...",
            "tabrion.Database.health: since 0.1, but it is a stub (its body is only pass or ...)",
        ),
        (
            "_sa/database.py",
            "def dialect(",
            "raise NotImplementedError",
            "tabrion.Database.dialect: since 0.1, but it is a stub",
        ),
        (
            "_sa/database.py",
            "def from_env(",
            "raise NotImplementedError()",
            "tabrion.Database.from_env: since 0.1, but it is a stub",
        ),
        ("check.py", "def check(", "pass", "tabrion.check.check: since 0.1, but it is a stub"),
        (
            "testing/postgres.py",
            "def postgres_server_version(",
            "raise NotImplementedError",
            "tabrion.testing.postgres_server_version: since 0.1, but it is a stub",
        ),
    ],
)
def test_no_stubs_turns_red_for_a_stub(tmp_path: Path, module: str, header: str, body: str, message: str) -> None:
    src = _stub_body(tmp_path, module, header, body)
    assert _run(src).returncode == 0  # the plain check does not look at bodies
    result = _run(src, "--no-stubs")
    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr, result.stderr


def test_no_stubs_turns_red_for_a_staged_name_of_this_minor(tmp_path: Path) -> None:
    """A name of the package's minor that still carries ``staged`` is only a frozen stub, not promoted."""
    data = json.loads(SURFACE.read_text(encoding="utf-8"))
    data["names"]["tabrion.sql.split_statements"]["staged"] = "tabrion.sql:split_statements"
    surface = tmp_path / "surface.json"
    surface.write_text(json.dumps(data), encoding="utf-8")
    result = _run(SRC, "--no-stubs", "--surface", str(surface))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "tabrion.sql.split_statements: since 0.1, but only staged" in result.stderr, result.stderr


def test_no_stubs_reads_a_method_with_a_flush_left_string(tmp_path: Path) -> None:
    """Review L-1 of #46: a method holding a triple-quoted string with flush-left lines cannot be dedented alone; the
    check parses the whole module, so it neither crashes nor calls the method a stub."""
    header = "    def health(self, *, timeout: float = 5.0, deep: bool = False) -> Health:\n"
    flush_left = header + '        _sql = """\nSELECT 1\n"""\n'
    src = _mutated(tmp_path, [("_sa/database.py", header, flush_left)])
    result = _run(src, "--no-stubs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "consistent, no stubs" in result.stdout


def test_no_stubs_names_a_method_with_a_flush_left_string_that_is_a_stub(tmp_path: Path) -> None:
    header = "    def health(self, *, timeout: float = 5.0, deep: bool = False) -> Health:\n"
    stub = header + '        """Stub.\n\nflush-left line\n"""\n        raise NotImplementedError\n'
    src = _mutated(tmp_path, [("_sa/database.py", header, stub + "\n    def _old_health(self) -> None:\n")])
    result = _run(src, "--no-stubs")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "tabrion.Database.health: since 0.1, but it is a stub (its body only raises" in result.stderr
