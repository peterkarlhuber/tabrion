"""TAB-T-V05 (local part): Tabrion passes every ``sqlite`` and ``any`` vector of the Levuvo Database Profile.

The ``postgres`` vectors run in ``tests/postgres/test_conformance_vectors.py``. INFO vectors run as well; only their
level is not normative (SPEC.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conformance.actions import HANDLERS
from tests.golden.axentra_splitter import axentra_split_statements
from tests.support.conformance import Vector, load_all

pytestmark = pytest.mark.conformance

LOCAL = [v for v in load_all() if v.engine in ("sqlite", "any")]


def _needs(vector: Vector) -> None:
    if vector.args.get("driver") == "psycopg":
        pytest.importorskip("psycopg")


@pytest.mark.parametrize("vector", LOCAL, ids=[v.id for v in LOCAL])
def test_vector(vector: Vector, tmp_path: Path) -> None:
    _needs(vector)
    observed = HANDLERS[vector.action](vector, tmp_path)
    for key, expected in vector.expect.items():
        assert key in observed, f"{vector.id}: the action reports no {key!r}"
        assert observed[key] == expected, f"{vector.id} ({vector.rule}): {key}"


@pytest.mark.parametrize("vector", [v for v in LOCAL if v.action == "split_sql"], ids=lambda v: v.id)
def test_split_vectors_match_the_axentra_oracle(vector: Vector) -> None:
    """The sql-split expectations are Axentra's behavior (SPL-1): its verbatim splitter gives the same output."""
    assert axentra_split_statements(vector.args["sql"]) == vector.expect["statements"]
