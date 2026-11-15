"""TAB-023 (G-T1 condition C-6): ``scripts/api_surface.py`` freezes every signature and keeps 0.2 names out of 0.1
(and the names of a later minor out of the package's, ADR 0015; 0.3 names out of 0.2 until the 0.3 switch).

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
    # Since 0.2 (TAB-042) the 0.2 names are public; the staging mutations of 0.1 became these.
    "0.2 name no longer exported": (
        [("__init__.py", '    "Result",\n', "")],
        "tabrion.Result: in surface.json but no longer public",
    ),
    "0.2 mixin dropped from the public class": (
        [("_sa/database.py", "class Database(_DatabaseV02):", "class Database:")],
        "tabrion.Database.query: in surface.json but no longer public",
    ),
    "0.2 mixin method signature changed": (
        [("_sa/_v02.py", "batch_size: int = 500", "batch_size: int = 100")],
        "tabrion.Transaction.stream: changed",
    ),
    # Since 0.3 (master 0.3.0.dev0) the 0.3 names of TAB-XW-6 are public; the gate mutations of 0.2 became these
    # (ADR 0015 Consequences, as TAB-042 did for 0.2). The gate itself is probed by
    # test_a_name_of_a_later_minor_must_not_be_importable.
    "0.3 mixin dropped from the public class": (
        [
            (
                "_sa/database.py",
                "class Transaction(_TransactionV03, _TransactionV02):",
                "class Transaction(_TransactionV02):",
            )
        ],
        "tabrion.Transaction.execute_checked: in surface.json but no longer public",
    ),
    "0.3 error no longer exported": (
        [("errors.py", '    "RowCountMismatch",\n', "")],
        "tabrion.errors.RowCountMismatch: in surface.json but no longer public",
    ),
    "0.3 module name no longer exported": (
        [
            (
                "rowedit.py",
                '__all__ = ["delete_row", "insert_row", "update_row"]',
                '__all__ = ["delete_row", "insert_row"]',
            )
        ],
        "tabrion.rowedit.update_row: in surface.json but no longer public",
    ),
    "0.3 mixin method signature changed": (
        [("_sa/_v03.py", "max_affected: int | None = None,", "max_affected: int | None = 1,")],
        "tabrion.Transaction.execute_checked: changed",
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


def _minors() -> tuple[str, str]:
    """The package's minor and the one after it (``"0.3"``, ``"0.4"`` for ``0.3.0.dev0``)."""
    from tabrion._since import minor
    from tabrion._version import __version__

    major, current = minor(__version__)
    return f"{major}.{current}", f"{major}.{current + 1}"


@pytest.mark.parametrize(
    ("path", "staged", "edits", "message"),
    [
        (
            "tabrion.Transaction.execute_checked",
            "tabrion._sa._v03:_TransactionV03.execute_checked",
            [],
            "tabrion.Transaction.execute_checked: since {later}, but the {current} surface exports it",
        ),
        (
            "tabrion.rowedit.update_row",
            "tabrion._sa._rowedit:update_row",
            [],
            "tabrion.rowedit.update_row: since {later}, but the {current} surface exports it",
        ),
        (
            "tabrion.errors.RowCountMismatch",
            "tabrion.errors:RowCountMismatch",
            [("errors.py", '    "RowCountMismatch",\n', "")],
            "tabrion.errors.RowCountMismatch: since {later}, but it is importable from its public path",
        ),
    ],
)
def test_a_name_of_a_later_minor_must_not_be_importable(
    tmp_path: Path, path: str, staged: str, edits: list[Edit], message: str
) -> None:
    """ADR 0015 Consequences (the C-6 mechanism, kept after the 0.3 switch, ``tabrion._since``): a name whose ``since``
    is a later minor than the package's must not be exported, nor importable from its public path outside
    ``__all__``, even with a valid ``staged`` location. Below 0.3 the 0.3 names of TAB-XW-6 were these names."""
    current, later = _minors()
    data = json.loads(SURFACE.read_text(encoding="utf-8"))
    data["names"][path] = {**data["names"][path], "since": later, "staged": staged}
    surface = tmp_path / "surface.json"
    surface.write_text(json.dumps(data), encoding="utf-8")
    result = _run(_mutated(tmp_path, edits), "--surface", str(surface))
    assert result.returncode == 1, result.stdout + result.stderr
    assert message.format(later=later, current=current) in result.stderr, result.stderr
    assert "staged" not in result.stderr.replace("staged for a later minor", ""), result.stderr  # the stub matches


# --- G-S2 follow-up 7A: the names Studio's Data Editor imports are stable --------------------------------------------

CONTRACT = ROOT / "docs" / "api" / "contract.md"

STUDIO_DATA_EDITOR = {
    "tabrion.rowedit.insert_row": "tabrion.rowedit",
    "tabrion.rowedit.update_row": "tabrion.rowedit",
    "tabrion.rowedit.delete_row": "tabrion.rowedit",
    "tabrion.errors.RowCountMismatch": "tabrion.errors",
}
"""Each name and its module as section 0 of the contract lists it."""


def _overview_stability(module: str, short: str) -> set[str]:
    """The ``Stab.`` cells of the rows of contract section 0 whose module is ``module`` and that name ``short``."""
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("## 0. Overview of the public names", 1)[1].split("\n## ", 1)[0]
    found = set()
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].split(" ", 1)[0] == f"`{module}`" and f"`{short}`" in cells[1]:
            found.add(cells[3])
    return found


@pytest.mark.parametrize("path", sorted(STUDIO_DATA_EDITOR))
def test_studio_data_editor_names_are_stable(path: str) -> None:
    """Owner decision 7A (G-S2 follow-up): before Studio 1.1, the names its Data Editor imports are stable, because
    consumers use only ``S`` names (API stability policy S-2; Studio's ``check_tabrion_api.py --surface``). A change
    back to ``X`` must change this test, which ``api_surface.py --base`` alone would not notice (TODO TB-094)."""
    entry = json.loads(SURFACE.read_text(encoding="utf-8"))["names"][path]
    assert entry["stability"] == "S", entry
    assert entry["since"] == "0.3", entry
    assert "deprecated" not in entry, entry
    module, short = path.rsplit(".", 1)
    assert module == STUDIO_DATA_EDITOR[path]
    assert _overview_stability(module, short) == {"S"}, path


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
