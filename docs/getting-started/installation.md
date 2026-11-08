# Installation

Tabrion needs Python 3.11 or later. It is not on PyPI yet: every public release needs the owner's approval (G-R,
[ADR 0009](../adr/0009-distribution-and-versioning.md)). Until then, install it from Git, pinned to a commit.

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"
```

## Internal pre-release v0.1.0a1

`v0.1.0a1` (TABRION TAB-038) is an internal pre-release of `tabrion.errors`, `tabrion.sql` and `tabrion.config` for
integration branches that are **not merged** yet (rule RC-8,
[ADR 0009](../adr/0009-distribution-and-versioning.md)). It is a Git tag only: nothing is on PyPI and there is no
GitHub release.

On such a branch, pin the tag directly:

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@v0.1.0a1"
```

A library on such a branch declares a pre-release range, which pip needs to accept an alpha:

```toml
dependencies = ["tabrion>=0.1.0a1,<0.2"]
```

The rules:

- **Never on master.** A consumer's master carries only final ranges and pins (`tabrion>=0.1,<0.2`, a final tag).
  The architecture check AC-6 refuses a pre-release specifier or pin there.
- A final range rejects the alpha: `tabrion>=0.1,<0.2` does not accept `0.1.0a1` (probe
  `proben/versionierung/`), so a library on master cannot be installed with the a1 pin by accident. A pre-release
  range `>=0.1.0a1,<0.2` accepts `0.1.0a1`, later pre-releases and `0.1.0`, but never `0.2.0a1`.
- One Tabrion version per installation (K-34): every package of an installation pins the same tag.
- The first merge of a consumer with a Tabrion edge needs `v0.1.0` final (TAB-040); rebase the integration branch
  to the final range and pin then.
- Only the three modules above carry the a1 promise. The other modules in the tag are work in progress.

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
