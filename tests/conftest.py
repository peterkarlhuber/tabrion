"""Shared test configuration. Kept free of third-party imports: the job "core without extras" uses it too.

The suite activates Tabrion's own pytest plugin explicitly, as a consumer does (contract section 6; it needs only
pytest, and the PostgreSQL fixtures import psycopg when they run), pytest's ``pytester`` for the plugin tests, and the
skip guard of the CI jobs (``tests/ci/skipguard.py``, TODO TB-141).
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

# The packaging jobs run pytest without Tabrion installed (they test built wheels in fresh environments): the plugin is
# activated only where the package can be imported. The skip guard (TB-141, tests/ci/skipguard.py) needs only pytest; it
# stays inactive unless TABRION_SKIP_GUARD or --skip-guard selects a mode.
pytest_plugins = ["pytester", "tests.ci.skipguard", *(["tabrion.testing"] if find_spec("tabrion") is not None else [])]

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
