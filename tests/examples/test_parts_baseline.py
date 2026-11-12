"""``examples/parts-baseline`` (RA-A-3): the shared parts suite against the plain SQLAlchemy baseline.

The same ``PartsSuite`` as ``test_parts.py``, with an engine instead of a ``tabrion.Database``. ``BASELINE.deviations``
lists the tests the baseline fails, each with its reason; they run as strict ``xfail``, and the README of
``examples/parts-baseline`` reports them as measurements for RA-EVAL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
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
    write=parts_baseline.db.write_transaction,
    read=_read,
    not_current=parts_baseline.db.SchemaNotCurrent,
    integrity_error=sa.exc.IntegrityError,
    refused_value=lambda error: (
        isinstance(error, sa.exc.StatementError)
        and not isinstance(error, sa.exc.DBAPIError)
        and isinstance(error.orig, ValueError)
    ),
    constraint_name=lambda error: None,
    deviations={
        "test_violations_name_their_constraint": (
            "db.classify tells the kind of violation apart but not the constraint's name (Tabrion reads it from"
            " the PostgreSQL diagnostics and the SQLite message)"
        ),
    },
)


class TestPartsBaseline(PartsSuite):
    @pytest.fixture
    def app(self) -> App:
        return BASELINE


_RUN_WITHOUT_TABRION = """
import sys
from parts_baseline import create_engine, downgrade, require_current, upgrade
from parts_baseline.catalog import create_material, create_part, get_part

engine = create_engine("sqlite:///" + sys.argv[1])
upgrade(engine)  # loads env.py and every revision
require_current(engine)
create_material(engine, "STEEL", "Steel")
create_part(engine, "P-1", "Bolt", "STEEL")
get_part(engine, "P-1")
downgrade(engine, "base")
engine.dispose()
loaded = sorted(m for m in sys.modules if m.split(".")[0] == "tabrion")
print(loaded)
raise SystemExit(1 if loaded else 0)
"""


def test_the_baseline_imports_no_tabrion(tmp_path: Path) -> None:
    """Review L-1 of #35: the baseline is SQLAlchemy and Alembic alone. A subprocess that migrates up (loading
    ``env.py`` and the revisions), writes, reads and migrates down has loaded no ``tabrion`` module."""
    path = os.pathsep.join([str(EXAMPLES / "parts-baseline"), *sys.path])
    result = subprocess.run(
        [sys.executable, "-c", _RUN_WITHOUT_TABRION, str(tmp_path / "isolated.db")],
        env={**os.environ, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
