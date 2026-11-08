# Installation

Tabrion needs Python 3.11 or later. It is not on PyPI yet: every public release needs the owner's approval (G-R,
[ADR 0009](../adr/0009-distribution-and-versioning.md)). Until then, install it from Git, pinned to a commit.

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"
```

## Extras

| Extra | Adds | For |
|---|---|---|
| (none) | nothing | `tabrion.errors`, `tabrion.sql`, `tabrion.config`, `tabrion.sqlite`, `tabrion.check` |
| `postgres` | psycopg >=3.2,<4 | `tabrion.postgres` |
| `sqlalchemy` | SQLAlchemy >=2.0.30,<2.2 | layer B: `tabrion.Database` and friends |
| `migrations` | the `sqlalchemy` extra plus Alembic >=1.13,<2 | `tabrion.migrations` |
| `testing` | pytest >=8 | `tabrion.testing` |
| `all` | all of the above | |

```bash
pip install "tabrion[postgres,sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"
```

The `postgres` extra installs `psycopg` without a libpq build. Install `psycopg[binary]` or a system libpq next to
it, as psycopg's documentation describes.

A module whose extra is missing fails on import with a hint, for example:

```text
ImportError: tabrion.Database needs the extra "sqlalchemy": pip install "tabrion[sqlalchemy]"
```

The core imports no optional package: `import tabrion.sqlite` works in an environment without psycopg, SQLAlchemy
and Alembic. CI checks this on every pull request (job `core without extras`).

## Development install

```bash
git clone https://github.com/peterkarlhuber/tabrion
cd tabrion
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

See [CONTRIBUTING.md](https://github.com/peterkarlhuber/tabrion/blob/master/CONTRIBUTING.md) for the checks to run
before a pull request.
