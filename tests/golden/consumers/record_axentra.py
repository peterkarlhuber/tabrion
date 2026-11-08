"""Records Axentra's behavior for contract section 8.1 (run by ``scripts/record_golden.py`` in Axentra's venv).

Calls Axentra's own code: ``Database._configure``, ``Database.ping``, ``Database.role_info``/``check_role``,
``roles.inspect_role``, ``migrations._wait_for_lock``, ``events.schedules.LeaderLock`` and
``core.db_errors.safe_message``. Only the plumbing around them is new: a ``Database`` is built with
``object.__new__`` and the one attribute the method reads (``_timeout`` or ``pool``), since the class would open a
pool and run checks first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.golden.consumers import scenarios
from tests.golden.consumers._recorder import run


def records(admin_url: str, prefix: str, directory: Path) -> dict[str, Any]:
    from axentra.core.db_errors import safe_message
    from axentra.core.errors import ConfigurationError
    from axentra.data.database import Database
    from axentra.data.migrations import MIGRATION_LOCK, _wait_for_lock
    from axentra.data.roles import inspect_role
    from axentra.events.schedules import LeaderLock
    import psycopg
    from psycopg_pool import ConnectionPool

    def database(**attributes: Any) -> Any:
        db = object.__new__(Database)
        for name, value in attributes.items():
            setattr(db, name, value)
        return db

    def pooled(url: str) -> ConnectionPool:
        pool = ConnectionPool(url, min_size=1, max_size=1, open=False)
        pool.open(wait=True, timeout=10)
        return pool

    def info(url: str) -> dict[str, Any]:
        pool = pooled(url)
        try:
            return dict(database(pool=pool).role_info())
        finally:
            pool.close()

    def check(url: str, allow_superuser: bool, allow_table_owner: bool) -> bool:
        pool = pooled(url)
        try:
            database(pool=pool).check_role(allow_superuser=allow_superuser, allow_table_owner=allow_table_owner)
            return True
        except ConfigurationError:
            return False
        finally:
            pool.close()

    def inspect(owner_url: str, role: str, schema: str) -> dict[str, Any]:
        with psycopg.connect(owner_url, autocommit=True) as conn:
            return dict(inspect_role(conn, role, schema).as_dict())

    out: dict[str, Any] = {}
    for timeout in (5000, 0):
        with scenarios.Scratch(admin_url, prefix) as scratch:
            out[f"configure.statement_timeout_ms={timeout}"] = scenarios.configure_scenario(
                scratch, database(_timeout=timeout)._configure, autocommit=False
            )
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["ping"] = scenarios.ping_scenario(scratch, lambda pool: database(pool=pool).ping())
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["role_info.check_role"] = scenarios.role_scenario(scratch, info, check)
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["inspect_role"] = scenarios.inspect_scenario(scratch, inspect)
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["wait_for_lock"] = {
            "key": MIGRATION_LOCK,
            "result": scenarios.wait_lock_scenario(scratch, lambda conn, key: _wait_for_lock(conn), MIGRATION_LOCK),
        }
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["leader_lock"] = scenarios.leader_lock_scenario(scratch, LeaderLock, 4711)
    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["safe_message"] = scenarios.error_text_scenario(scratch, safe_message)
    return out


if __name__ == "__main__":
    run(records)
