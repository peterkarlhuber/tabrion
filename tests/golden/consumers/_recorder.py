"""What every recorder shares: it runs in a consumer's throwaway virtual environment, never next to Tabrion."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _environment(admin_url: str) -> dict[str, str]:
    """The versions the records depend on: Python, the SQLite library, the PostgreSQL server."""
    import platform
    import sqlite3

    found = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version}
    try:
        import psycopg

        with psycopg.connect(admin_url, connect_timeout=10) as conn:
            found["postgres"] = str(conn.execute("SHOW server_version_num").fetchone()[0])  # type: ignore[index]
    except Exception:  # noqa: S110 - a recorder without PostgreSQL (nodestro-api) records no server version
        pass
    return found


def run(records: Callable[[str, str, Path], dict[str, Any]]) -> None:
    """Print ``{"records": records(admin_url, prefix, directory), "environment": ...}`` as JSON on stdout.

    ``scripts/record_golden.py`` sets ``TABRION_GOLDEN_ADMIN_URL`` (a PostgreSQL admin URL with CREATEDB and
    CREATEROLE) and ``TABRION_GOLDEN_PREFIX`` (the prefix of everything the scenarios create on the server)."""
    if importlib.util.find_spec("tabrion") is not None:
        raise SystemExit("the recorder must run without Tabrion (a throwaway environment with the consumer only)")
    admin_url = os.environ["TABRION_GOLDEN_ADMIN_URL"]
    prefix = os.environ["TABRION_GOLDEN_PREFIX"]
    with tempfile.TemporaryDirectory(prefix="tabrion-golden-") as directory:
        result = records(admin_url, prefix, Path(directory))
    json.dump({"records": result, "environment": _environment(admin_url)}, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
