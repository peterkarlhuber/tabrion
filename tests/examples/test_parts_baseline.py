"""``examples/parts-baseline`` (RA-A-3): the shared parts suite against the plain SQLAlchemy baseline.

The same ``PartsSuite`` as ``test_parts.py``, with an engine instead of a ``tabrion.Database``. ``BASELINE.deviations``
lists the tests the baseline fails, each with its reason; they run as strict ``xfail``, and the README of
``examples/parts-baseline`` reports them as measurements for RA-EVAL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import subprocess
import sys
from typing import Any

import pytest

pytest.importorskip("parts_baseline", reason="examples/ is not in the sdist")
pytest.importorskip("alembic")

import parts_baseline.catalog
import parts_baseline.db
import parts_baseline.errors
import parts_baseline.schema
import sqlalchemy as sa

from .conftest import EXAMPLES
from .parts_suite import App, PartsSuite

pytestmark = pytest.mark.filterwarnings(
    "ignore:Empty parameter sequence passed to execute:sqlalchemy.exc.SADeprecationWarning"
)


@contextmanager
def _read(engine: sa.Engine) -> Iterator[sa.Connection]:
    with engine.connect() as connection:
        yield connection


def _open(url: Any, search_path: str | None) -> sa.Engine:
    return parts_baseline.db.create_engine(url, search_path=search_path)


BASELINE = App(
    name="parts-baseline (plain SQLAlchemy)",
    directory=EXAMPLES / "parts-baseline",
    db=parts_baseline.db,
    catalog=parts_baseline.catalog,
    errors=parts_baseline.errors,
    schema=parts_baseline.schema,
    open=_open,
    close=sa.Engine.dispose,
    engine=lambda engine: engine,
    write=sa.Engine.begin,
    read=_read,
    not_current=parts_baseline.db.SchemaNotCurrent,
    deviations={
        "test_a_failing_upgrade_leaves_nothing_behind[sqlite]": (
            "pysqlite opens a transaction only before INSERT, UPDATE and DELETE, so engine.begin() does not cover"
            " Alembic's DDL: the tables of 0001 stay behind with an empty version table"
        ),
    },
)


class TestPartsBaseline(PartsSuite):
    @pytest.fixture
    def app(self) -> App:
        return BASELINE


def test_the_baseline_imports_no_tabrion() -> None:
    """The baseline is SQLAlchemy and Alembic alone: importing all of it loads no ``tabrion`` module."""
    code = (
        "import sys; import parts_baseline, parts_baseline.catalog, parts_baseline.db; "
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'tabrion'); "
        "print(loaded); raise SystemExit(1 if loaded else 0)"
    )
    path = os.pathsep.join([str(EXAMPLES / "parts-baseline"), *sys.path])
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
