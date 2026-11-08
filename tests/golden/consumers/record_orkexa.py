"""Records Orkexa's behavior for contract section 8.3 (run by ``scripts/record_golden.py`` in Orkexa's venv).

Calls Orkexa's own code: ``backends.postgres.PsycopgSource``, ``_storage.connect_private_database`` and the
transaction helpers of ``SQLiteFileStore``, ``SQLiteMemory`` and ``SQLiteIndex``; and reads the variable names of
``tests/backends/postgres/support.py`` from the source at the same commit (``TABRION_GOLDEN_SOURCE``, a checkout).
Plumbing only: a helper runs on a stand-in object with the one attribute it reads (``_connection``), since the
classes would create their tables first.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

from tests.golden.consumers import scenarios
from tests.golden.consumers._recorder import run


def records(admin_url: str, prefix: str, directory: Path) -> dict[str, Any]:
    from orkexa._storage import connect_private_database
    from orkexa.backends.postgres import PsycopgSource
    from orkexa.backends.sqlite.files import SQLiteFileStore
    from orkexa.backends.sqlite.index import SQLiteIndex
    from orkexa.backends.sqlite.memory import SQLiteMemory

    def helper(method: Any) -> Callable[[sqlite3.Connection], Any]:
        return lambda conn: method(SimpleNamespace(_connection=conn))

    def sub(name: str) -> Path:  # a directory per scenario (the scenarios reuse file names)
        path = directory / name
        path.mkdir()
        return path

    out: dict[str, Any] = {}
    out["sqlite.open"] = scenarios.sqlite_open_scenario(sub("open"), connect_private_database)
    out["sqlite.symlink"] = scenarios.sqlite_symlink_scenario(sub("link"), connect_private_database)
    for label, method in (
        ("files", SQLiteFileStore._transaction),
        ("memory", SQLiteMemory._transaction),
        ("index", SQLiteIndex._write),
    ):
        out[f"sqlite.transaction.{label}"] = scenarios.sqlite_transaction_scenario(
            sub(label), connect_private_database, helper(method)
        )
    out["sqlite.read.index"] = scenarios.sqlite_read_scenario(
        sub("read"), connect_private_database, helper(SQLiteIndex._read)
    )

    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["source"] = scenarios.source_scenario(scratch, lambda url: PsycopgSource(url, max_connections=1))

    source = Path(os.environ["TABRION_GOLDEN_SOURCE"]) / "tests" / "backends" / "postgres" / "support.py"
    out["testing.environment"] = scenarios.environment_names(source.read_text(encoding="utf-8"))
    return out


if __name__ == "__main__":
    run(records)
