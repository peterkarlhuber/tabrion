"""Records Nodestro's behavior for contract section 8.2 (run by ``scripts/record_golden.py`` in Nodestro's venv).

Calls nodestro-runtime's own code: ``SQLiteStore`` (opening, ``transaction``, ``migrate``, ``snapshot``),
``PostgresDatabase._configure``, its session locks, ``role_info``, ``inspect_role`` and ``pgvector_status``, and
``ServerSettings.from_env`` for ``NODESTRO_DATABASE_URL``/``_FILE``; and reads the variable names of
``tests/postgres_support.py`` from the source at the same commit (``TABRION_GOLDEN_SOURCE``, a checkout). Plumbing
only: ``_configure`` runs on an object built with ``object.__new__`` and the three attributes it reads.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sqlite3
from typing import Any

from tests.golden.consumers import scenarios
from tests.golden.consumers._recorder import run


def records(admin_url: str, prefix: str, directory: Path) -> dict[str, Any]:
    from nodestro_runtime.server_settings import ServerSettings
    from nodestro_runtime.storage import SQLiteStore
    from nodestro_runtime.storage_postgres import PostgresDatabase, inspect_role, pgvector_status

    out: dict[str, Any] = {}

    # --- SQLite --------------------------------------------------------------------------------------------------
    stores: dict[int, SQLiteStore] = {}

    def open_(path: Path) -> sqlite3.Connection:
        store = SQLiteStore(path)
        conn: sqlite3.Connection = store._connection
        stores[id(conn)] = store
        return conn

    out["sqlite.open"] = scenarios.sqlite_open_scenario(directory, open_)
    out["sqlite.transaction"] = scenarios.sqlite_transaction_scenario(
        directory, open_, lambda conn: stores[id(conn)].transaction()
    )

    def migrate(conn: sqlite3.Connection, migrations: Any, target: int, backup: Any) -> Any:
        return stores[id(conn)].migrate(before_migration=backup, migrations=migrations, target=target)

    out["sqlite.migrate"] = scenarios.sqlite_migrate_scenario(directory, open_, migrate)
    out["sqlite.snapshot"] = scenarios.sqlite_backup_scenario(
        directory, open_, lambda conn, target: stores[id(conn)].snapshot(target)
    )
    links = directory / "links"
    links.mkdir()
    out["sqlite.symlink"] = scenarios.sqlite_symlink_scenario(links, open_)
    for store in stores.values():
        store.close()

    # --- NODESTRO_DATABASE_URL / _FILE -------------------------------------------------------------------------------
    # Every other required setting is given (fake values), so ``from_env`` succeeds or fails on the URL alone.
    base = {
        "NODESTRO_PUBLIC_ORIGIN": "http://localhost:8000",
        "NODESTRO_OIDC_ISSUER": "http://localhost:9000/issuer",
        "NODESTRO_OIDC_CLIENT_ID": "golden",
        "NODESTRO_OIDC_CLIENT_SECRET": "golden-client-secret",
        "NODESTRO_SECRET_KEY": "A" * 44,
        "NODESTRO_AXENTRA": "off",
    }

    def read(environ: Mapping[str, str]) -> Any:
        return ServerSettings.from_env({**base, **environ}).database_url

    out["url_from_env"] = scenarios.url_file_scenario(directory, read, "NODESTRO_DATABASE_URL")

    # --- PostgreSQL ------------------------------------------------------------------------------------------------
    for schema in (None, "nodestro"):
        with scenarios.Scratch(admin_url, prefix) as scratch:
            if schema:
                with scratch.admin() as conn:
                    conn.execute(f"CREATE SCHEMA {schema}")
            db = object.__new__(PostgresDatabase)
            db.schema = schema
            db.statement_timeout = 30.0
            db.idle_in_transaction_timeout = 300.0
            out[f"configure.schema={schema}"] = scenarios.configure_scenario(scratch, db._configure, autocommit=True)

    class Locks:
        def __init__(self, url: str) -> None:
            self.db = PostgresDatabase(url, pool_size=1)

        def try_(self, key: int) -> bool:
            return bool(self.db.try_session_lock(key))

        def holds(self, key: int) -> bool:
            return bool(self.db.holds_session_lock(key))

        def release(self, key: int) -> None:
            self.db.release_session_lock(key)

        def close(self) -> None:
            self.db.close()

    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["session_lock"] = scenarios.session_lock_scenario(scratch, Locks, 4711)

    def info(url: str) -> dict[str, Any]:
        db = PostgresDatabase(url, schema="public", pool_size=1)
        try:
            return dict(db.role_info())
        finally:
            db.close()

    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["role_info"] = scenarios.role_scenario(scratch, info, None)  # no start check of its own

    def inspect(owner_url: str, role: str, schema: str) -> dict[str, Any]:
        db = PostgresDatabase(owner_url, schema=schema, pool_size=1)
        try:
            return dict(inspect_role(db, role))
        finally:
            db.close()

    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["inspect_role"] = scenarios.inspect_scenario(scratch, inspect)

    def vector(url: str) -> dict[str, Any]:
        from psycopg.conninfo import conninfo_to_dict

        params = conninfo_to_dict(url)
        options = str(params.pop("options", ""))
        schema = options.rsplit("=", 1)[-1] if "search_path" in options else None
        db = PostgresDatabase(scenarios.with_params(url, options=""), schema=schema, pool_size=1)
        try:
            return dict(pgvector_status(db))
        finally:
            db.close()

    with scenarios.Scratch(admin_url, prefix) as scratch:
        out["pgvector_status"] = scenarios.vector_scenario(scratch, vector)

    # --- tests/postgres_support.py -----------------------------------------------------------------------------------
    source = Path(os.environ["TABRION_GOLDEN_SOURCE"]) / "tests" / "postgres_support.py"
    out["testing.environment"] = scenarios.environment_names(source.read_text(encoding="utf-8"))
    return out


if __name__ == "__main__":
    run(records)
