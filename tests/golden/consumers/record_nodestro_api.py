"""Records nodestro-api's database check for contract section 8.2 (run by ``scripts/record_golden.py`` in its venv).

Calls nodestro-api's own ``diagnostics._database`` on a stand-in for the ``Nodestro`` object with the one attribute
it reads (``sqlite_store.database``), and keeps the outcome (``status`` and ``code``); the message names the path
and the SQLite version of the machine.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.golden.consumers import scenarios
from tests.golden.consumers._recorder import run


def records(admin_url: str, prefix: str, directory: Path) -> dict[str, Any]:
    from nodestro_api.diagnostics import _database

    def check(path: Path) -> dict[str, Any]:
        result = _database(SimpleNamespace(sqlite_store=SimpleNamespace(database=str(path))))
        return {"status": result.status, "code": result.code}

    return {"diagnostics.database": scenarios.sqlite_check_scenario(directory, check)}


if __name__ == "__main__":
    run(records)
