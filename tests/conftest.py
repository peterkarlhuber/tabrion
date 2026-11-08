"""Shared test configuration. Kept free of third-party imports: the job "core without extras" uses it too.

The suite activates Tabrion's own pytest plugin explicitly, as a consumer does (contract section 6; it needs only
pytest, and the PostgreSQL fixtures import psycopg when they run), and pytest's ``pytester`` for the plugin tests.
"""

from __future__ import annotations

from pathlib import Path

pytest_plugins = ["tabrion.testing", "pytester"]

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
