"""TAB-041: the code examples of the user guides run (SQLite and layer B; PostgreSQL pages run in the pg jobs).

The harness is described in ``tests/docs/harness.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.docs.harness import DOCS, ROOT, engines, guide_pages, parse, run_page

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")


@pytest.mark.parametrize("page", [p for p in guide_pages() if "sqlite" in engines(p)], ids=lambda p: p.stem)
def test_examples_run(page: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SQLite and layer-B pages; a page for both engines gets a SQLite file as ``DATABASE_URL``."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'guide.db').as_posix()}")
    assert run_page(page, tmp_path) > 0


@pytest.mark.parametrize("page", guide_pages(), ids=lambda p: p.stem)
def test_page_parses_and_is_in_nav(page: Path) -> None:
    """Every guide has at least one executed example and is listed in the MkDocs navigation."""
    assert any(step.lang in {"python", "pycon"} and step.file is None for step in parse(page)), page.name
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert str(page.relative_to(DOCS)) in nav, f"{page.name} is not in mkdocs.yml nav"


def test_unknown_language_and_directive_fail(tmp_path: Path) -> None:
    """Negative probe: a block the harness would neither run nor show, or a stray directive, fails the page."""
    for text in (
        "```py\nprint(1)\n```\n",
        "<!-- docs-test: skip -->\n```python\nprint(1)\n```\n",
        "<!-- docs-test: posix -->\nSome text.\n```python\nprint(1)\n```\n",
        "<!-- docs-test: file=x.py -->\n```bash\necho\n```\n",
        "~~~python\nraise RuntimeError('probe')\n~~~\n",
        "  ~~~pycon\n  >>> 1\n  2\n  ~~~\n",
    ):
        page = tmp_path / "probe.md"
        page.write_text(text, encoding="utf-8")
        with pytest.raises(AssertionError):
            parse(page)


def test_failing_example_fails(tmp_path: Path) -> None:
    """Negative probe: wrong doctest output and a raising script block both fail the run."""
    page = tmp_path / "probe.md"
    page.write_text("```pycon\n>>> 1 + 1\n3\n```\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="doctest failed"):
        run_page(page, tmp_path)
    page.write_text("```python\nraise RuntimeError('probe')\n```\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="probe"):
        run_page(page, tmp_path)


def test_unused_file_block_fails(tmp_path: Path) -> None:
    """Negative probe (review L-1): a ``file=`` block that no later step uses fails the page."""
    page = tmp_path / "probe.md"
    work = tmp_path / "work"
    work.mkdir()
    page.write_text("<!-- docs-test: file=helper.py -->\n```python\nraise RuntimeError('never run')\n```\n")
    with pytest.raises(AssertionError, match="no step used"):
        run_page(page, work)
    # A pytest step does not use a file it does not collect.
    page.write_text(
        "<!-- docs-test: file=helper.py -->\n```python\nVALUE = 1\n```\n"
        "<!-- docs-test: file=test_probe.py -->\n```python\ndef test_ok():\n    pass\n```\n"
        "<!-- docs-test: pytest -->\n"
    )
    with pytest.raises(AssertionError, match=r"helper\.py"):
        run_page(page, work)


def test_used_file_blocks_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file executed in this process, and a test file pytest collects, count as used."""
    monkeypatch.delenv("TABRION_REQUIRE_POSTGRES", raising=False)
    page = tmp_path / "probe.md"
    work = tmp_path / "work"
    work.mkdir()
    page.write_text(
        "<!-- docs-test: file=pkg/helper.py -->\n```python\nVALUE = 2\n```\n"
        "<!-- docs-test: file=test_probe.py -->\n```python\nimport pytest\n\n\n"
        "def test_ok():\n    pass\n\n\ndef test_skip():\n    pytest.skip('probe')\n```\n"
        "<!-- docs-test: pytest -->\n"
        "```pycon\n>>> import runpy\n>>> runpy.run_path('pkg/helper.py')['VALUE']\n2\n```\n"
    )
    assert run_page(page, work) == 2
    # In the pg jobs, a skipped test fails the pytest step (review L-2).
    monkeypatch.setenv("TABRION_REQUIRE_POSTGRES", "1")
    with pytest.raises(AssertionError, match="skipped"):
        run_page(page, work)
