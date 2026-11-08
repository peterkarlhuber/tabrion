"""Run the code examples of the user guides (TAB-041: every code example is executed as a doctest).

Each guide is a Markdown page under ``docs/guides/`` (plus ``docs/getting-started/quickstart.md``). Its fenced code
blocks run in page order, in one namespace, in an empty working directory:

- a ``pycon`` block (``>>>`` prompts) runs through :mod:`doctest`, and its output must match
  (``ELLIPSIS`` and ``NORMALIZE_WHITESPACE``);
- a ``python`` block runs as a script;
- ``bash``, ``console``, ``text``, ``toml``, ``ini`` and ``sql`` blocks are shown, not run.

Directives are HTML comments, which MkDocs does not render:

- ``<!-- docs-test: postgres -->`` on a line of its own marks the page as needing PostgreSQL; its examples read the
  environment variable ``DATABASE_URL``, which the test sets to a fresh schema of the test server
  (``tests/postgres/test_guides_postgres.py``). The other pages run in the ordinary unit job.
  ``<!-- docs-test: sqlite-and-postgres -->`` runs the page twice: in the unit job with ``DATABASE_URL`` a SQLite
  file, and in the pg jobs on a fresh schema; its outputs must hold on both engines.
- ``<!-- docs-test: file=<path> -->`` right before a block writes the block to ``<path>`` in the working directory
  instead of running it (a ``conftest.py``, an Alembic revision). The file must be used: a later ``pytest`` step
  collects it (a ``conftest.py``, or a ``test_*.py`` that appears in pytest's report), or a later step executes its
  code in this process (Alembic's ``env.py`` and revisions). A file that nothing uses fails the page.
- ``<!-- docs-test: posix -->`` right before a block runs it only on POSIX (file modes).
- ``<!-- docs-test: pytest -->`` on a line of its own runs ``pytest`` in the working directory at that point; it must
  pass. With ``TABRION_REQUIRE_POSTGRES=1`` (the pg jobs) it must also skip nothing, so the PostgreSQL examples of a
  ``sqlite-and-postgres`` page run there.

Nothing is skipped silently: a ``python`` or ``pycon`` block is either run or written as a file that is then used,
and an unknown language or directive, or a ``~~~`` fence (which MkDocs renders but this parser would not see), fails
the test (``parse``). After the page, every ``sqlite3.Connection`` and ``tabrion.Database`` left in its namespace is
closed, so a failing page does not leak warnings into later tests.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass
import doctest
import io
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

RUN = frozenset({"python", "pycon"})
SHOWN = frozenset({"bash", "console", "text", "toml", "ini", "sql"})

_FENCE = re.compile(r"^(?P<indent> *)```(?P<lang>[A-Za-z0-9_+-]*)[^\n]*$")
_TILDE_FENCE = re.compile(r"^ *~~~")
_DIRECTIVE = re.compile(r"^ *<!-- docs-test: (?P<body>[^>]*?) -->$")
_FLAGS = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE
_ENGINES = {
    "sqlite": frozenset({"sqlite"}),
    "postgres": frozenset({"postgres"}),
    "sqlite-and-postgres": frozenset({"sqlite", "postgres"}),
}


def guide_pages() -> list[Path]:
    """Every page whose examples must run: the guides and the quickstart."""
    return [DOCS / "getting-started" / "quickstart.md", *sorted((DOCS / "guides").glob("*.md"))]


def engines(page: Path) -> frozenset[str]:
    """Where the page's examples run: ``sqlite`` (the default), ``postgres``, or both."""
    for line in page.read_text(encoding="utf-8").splitlines():
        directive = _DIRECTIVE.match(line)
        if directive is not None and directive["body"] in _ENGINES:
            return _ENGINES[directive["body"]]
    return _ENGINES["sqlite"]


@dataclass(frozen=True)
class Step:
    """One unit of a page: a code block (``lang``, ``code``) or a ``pytest`` run (``lang == "pytest"``)."""

    lang: str
    code: str
    line: int
    file: str | None = None
    posix_only: bool = False


def parse(page: Path) -> list[Step]:
    """The steps of ``page`` in order; raises ``AssertionError`` on an unknown language or directive."""
    lines = page.read_text(encoding="utf-8").splitlines()
    steps: list[Step] = []
    pending: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        directive = _DIRECTIVE.match(line)
        if directive is not None:
            body = directive["body"]
            if body in _ENGINES:
                pass
            elif body == "pytest":
                steps.append(Step("pytest", "", index + 1))
            elif body == "posix":
                pending["posix"] = "1"
            elif body.startswith("file="):
                pending["file"] = body.removeprefix("file=")
            else:
                raise AssertionError(f"{page.name}:{index + 1}: unknown directive {body!r}")
            index += 1
            continue
        if _TILDE_FENCE.match(line):
            raise AssertionError(f"{page.name}:{index + 1}: use a backtick fence; a ~~~ block would not be checked")
        fence = _FENCE.match(line)
        if fence is None:
            if line.strip() and pending:
                raise AssertionError(f"{page.name}:{index + 1}: a directive must stand right before a code block")
            index += 1
            continue
        indent, lang = fence["indent"], fence["lang"]
        start = index + 1
        index += 1
        body_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != "```":
            body_lines.append(lines[index][len(indent) :] if lines[index].startswith(indent) else lines[index])
            index += 1
        if index >= len(lines):
            raise AssertionError(f"{page.name}:{start}: unterminated code block")
        index += 1
        code = "\n".join(body_lines) + "\n"
        if lang not in RUN | SHOWN:
            raise AssertionError(f"{page.name}:{start}: code block language {lang!r} is neither run nor shown")
        if lang in SHOWN:
            if pending:
                raise AssertionError(f"{page.name}:{start}: a directive before a {lang} block has no effect")
            continue
        steps.append(Step(lang, code, start, pending.get("file"), "posix" in pending))
        pending = {}
    if pending:
        raise AssertionError(f"{page.name}: a directive at the end of the page has no block")
    return steps


@contextlib.contextmanager
def _isolated(path: Path) -> Iterator[None]:
    """Run in ``path`` and restore the working directory and the environment afterwards."""
    before = Path.cwd()
    environ = dict(os.environ)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)
        os.environ.clear()
        os.environ.update(environ)


_executed: set[Path] | None = None
"""While a page runs: the files whose code was executed in this process (``exec`` audit events)."""
_hook_installed = False


def _audit(event: str, args: tuple[object, ...]) -> None:
    if _executed is not None and event == "exec" and args:
        filename = getattr(args[0], "co_filename", None)
        if isinstance(filename, str) and not filename.startswith("<"):
            _executed.add(Path(filename).resolve())


@contextlib.contextmanager
def _tracking_exec() -> Iterator[set[Path]]:
    """Record the files executed while the block runs (an audit hook cannot be removed, so it is installed once)."""
    global _executed, _hook_installed
    if not _hook_installed:
        sys.addaudithook(_audit)
        _hook_installed = True
    _executed = set()
    try:
        yield _executed
    finally:
        _executed = None


def _close_leftovers(namespace: dict[str, object]) -> None:
    """Close the connections and databases a page left open (a failing page must not leak into later tests)."""
    from tabrion import Database

    for value in list(namespace.values()):
        if isinstance(value, sqlite3.Connection | Database):
            with contextlib.suppress(Exception):
                value.close()


def _collected_by_pytest(file: Path) -> bool:
    return file.name == "conftest.py" or file.name.startswith("test_") or file.name.endswith("_test.py")


def run_page(page: Path, workdir: Path) -> int:
    """Run every step of ``page`` in ``workdir``; returns the number of examples run. Fails on the first error."""
    steps = parse(page)
    namespace: dict[str, object] = {"__name__": f"docs_{page.stem.replace('-', '_')}"}
    runner = doctest.DocTestRunner(optionflags=_FLAGS)
    parser = doctest.DocTestParser()
    ran = 0
    unused: dict[Path, str] = {}
    """Files written by ``file=`` blocks that no step has used yet, with where they were written."""
    with _isolated(workdir), _tracking_exec() as executed:
        try:
            for step in steps:
                where = f"{page.relative_to(ROOT) if page.is_relative_to(ROOT) else page.name}:{step.line}"
                if step.posix_only and os.name != "posix":
                    continue
                if step.lang == "pytest":
                    result = subprocess.run(  # fixed arguments
                        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "-W", "error"],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                    )
                    assert result.returncode == 0, f"{where}: pytest failed\n{result.stdout}\n{result.stderr}"
                    if os.environ.get("TABRION_REQUIRE_POSTGRES") == "1":
                        assert "SKIPPED" not in result.stdout, f"{where}: pytest skipped tests\n{result.stdout}"
                    for file in [f for f in unused if _collected_by_pytest(f)]:
                        if file.name == "conftest.py" or file.name in result.stdout:
                            del unused[file]
                    ran += 1
                    continue
                if step.file is not None:
                    target = (workdir / step.file).resolve()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(step.code, encoding="utf-8")
                    unused[target] = f"{where} ({step.file})"
                    continue
                if step.lang == "python":
                    exec(compile(step.code, where, "exec"), namespace)  # noqa: S102  # the repository's own examples
                    ran += 1
                else:
                    test = parser.get_doctest(step.code, namespace, where, str(page), step.line - 1)
                    out = io.StringIO()
                    result_ = runner.run(test, out=out.write, clear_globs=False)
                    namespace.update(test.globs)  # DocTest works on a copy; the next block sees what this one defined
                    assert result_.failed == 0, f"{where}: doctest failed\n{out.getvalue()}"
                    assert result_.attempted > 0, f"{where}: a pycon block without a >>> example"
                    ran += 1
                for file in executed & unused.keys():
                    del unused[file]
        finally:
            _close_leftovers(namespace)
    assert not unused, f"file= blocks that no step used: {sorted(unused.values())}"
    return ran
