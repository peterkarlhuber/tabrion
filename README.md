<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/peterkarlhuber/tabrion/master/assets/branding/tabrion-logo-on-dark.png">
    <img src="https://raw.githubusercontent.com/peterkarlhuber/tabrion/master/assets/branding/tabrion-logo.png" alt="Tabrion" width="400">
  </picture>
</p>

# Tabrion

Small, secure database foundations for SQLite and PostgreSQL in Python.

> **Status: pre-alpha, milestone M1 ("foundation").** The public API is frozen as typed stubs for review (gate
> G-T1); every function raises `NotImplementedError`. The implementation follows in milestone M2 (Tabrion 0.1).
> Tabrion is distributed through Git: consumers pin a full commit SHA. A PyPI publication is not planned for now.

Tabrion makes the safe setting the default and keeps the established tools visible. It is a library that runs in
your process: no server, no service, no own connection pool. It is not an ORM, not a query language and not a
replacement for SQLAlchemy or psycopg.

What it provides (see the [specification](docs/specification.md)):

- **Safe connections and files**: SQLite files created with mode 0600 and no symlinks; WAL, foreign keys, busy
  timeout and `trusted_schema=OFF` by default; PostgreSQL session timeouts and `application_name` in one round trip.
- **Correct transactions**: SQLite writes with `BEGIN IMMEDIATE` (no lost updates), read transactions that cannot
  write, no silent retry of a statement whose outcome is unknown.
- **Value-free errors**: a classification of every driver error by SQLSTATE or SQLite code, and messages that never
  contain parameter values, `DETAIL` lines or passwords.
- **Role checks and grants**: refuse superuser, `BYPASSRLS` and table-owning runtime roles; grant DML-only runtime
  roles idempotently.
- **Diagnosis** with stable finding codes, and **test fixtures** for pytest.

## Two layers, one distribution

| Layer | Modules | Needs |
|---|---|---|
| A, driver core | `tabrion.errors`, `tabrion.sql`, `tabrion.config`, `tabrion.sqlite`, `tabrion.check` | the standard library only |
| A, PostgreSQL | `tabrion.postgres` | extra `postgres` (psycopg 3.2+) |
| B, SQLAlchemy integration | `tabrion.Database`, `Profile`, `Transaction`, `tabrion.migrations` | extras `sqlalchemy`, `migrations` |
| Test support | `tabrion.testing` | extra `testing` (pytest) |

## Installation

Install from Git (Python 3.11 to 3.14); reproducible installs pin a full commit SHA
([installation](docs/getting-started/installation.md)):

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion.git"               # core, no dependency
pip install "tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion.git"     # + psycopg
pip install "tabrion[sqlalchemy,migrations] @ git+https://github.com/peterkarlhuber/tabrion.git"
```

Extras: `postgres`, `sqlalchemy`, `migrations` (implies `sqlalchemy`), `testing`, `all`. Every combination is
installable. psycopg is installed without a binary or pool extra; choose `psycopg[binary]` or `psycopg[c]` in your
product.

## Examples (target API, not implemented yet)

Layer A, SQLite:

```python
from tabrion.sqlite import connect, write_transaction

conn = connect("measurements.db")  # 0600, WAL, foreign keys, busy timeout 30 s
with write_transaction(conn):  # BEGIN IMMEDIATE ... COMMIT
    conn.execute("INSERT INTO measurement (device_id, value) VALUES (?, ?)", ("m-1", 21.5))
```

Layer B, SQLite or PostgreSQL with the same code:

```python
from tabrion import Database, Profile

with Database.from_env("PARTS_DATABASE_URL", profile=Profile.default()) as db:
    with db.transaction() as tx:
        tx.execute("INSERT INTO parts (id, material) VALUES (:id, :material)", {"id": 1001, "material": "Aluminium"})
    parts = db.fetch_all("SELECT id, material FROM parts ORDER BY id")
```

## Documentation

- [Specification](docs/specification.md) and [API contract](docs/api/contract.md)
- [Architecture decision records](docs/adr/README.md)
- [Program status](docs/program/STATUS.md), [risk register](docs/program/risks.md), [open work](docs/TODO.md)

## Contributing, security, support

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [SUPPORT.md](SUPPORT.md) and the
[code of conduct](CODE_OF_CONDUCT.md).

## License

MIT, see [LICENSE](LICENSE). The name "Tabrion" and the logos are not covered by the MIT license; see
[TRADEMARKS.md](TRADEMARKS.md). psycopg (extra `postgres`) is LGPL-3.0 and is installed separately, never bundled.
