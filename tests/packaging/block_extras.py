"""A pytest plugin that blocks the optional dependencies before any test module is imported (05 section 2.3, AC-3).

``pytest -p tests.packaging.block_extras ...`` makes ``import psycopg``, ``import sqlalchemy``, ``import alembic``
and ``import psycopg_pool`` fail as if the extras were not installed.
"""

from __future__ import annotations

import sys

from tests.packaging.blocking import BLOCKED

for _name in BLOCKED:
    sys.modules[_name] = None  # type: ignore[assignment]
