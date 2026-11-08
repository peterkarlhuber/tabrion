# Installation

Tabrion needs Python 3.11 or later. It is distributed through Git: install it pinned to a full commit SHA. A PyPI
publication is not planned for now; one would need the owner's approval G-R for every version
([ADR 0009](../adr/0009-distribution-and-versioning.md), amendment of 2026-11-08).

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"
```

## Final versions

A final version is the merge commit of the version-bump pull request that sets a final package version; `v0.1.0`
final (TABRION TAB-040) is the one that sets `0.1.0`. Its full SHA is recorded here, in the CHANGELOG section of the
version and in `docs/program/STATUS.md` (TAB-040), and the next pull request bumps master to the next `.dev0`
version (`0.1.1.dev0`), so no other commit builds as `0.1.0`. Consumers pin that commit by its full SHA; a tag
`v0.1.0` on it is an optional label, never what a pin names. `0.1.0` is not released yet, so no SHA is recorded.

Pin only a recorded release commit. pip does not check that: it accepts a pin on any master commit whose version is
inside the range, for example `0.1.1.dev0`; only the architecture check AC-6 (planned, QA-15) refuses it. Always
supply the pin: Tabrion is not on PyPI, so an install without it would ask the package index for the name `tabrion`.

A consumer's CI installs Tabrion from that pin (for a library, the requirements file
`requirements/tabrion.txt`, installed with `-r`; ADR 0009), not from PyPI:

```text
tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion@<full commit sha of a final version>
```

## Internal pre-release v0.1.0a1

`v0.1.0a1` (TABRION TAB-038) is an internal pre-release of `tabrion.errors`, `tabrion.sql` and `tabrion.config` for
integration branches that are **not merged** yet (rule RC-8,
[ADR 0009](../adr/0009-distribution-and-versioning.md)). It is a commit only: nothing is on PyPI and there is no
GitHub release. An annotated tag `v0.1.0a1` on it is an optional label and may be missing on GitHub.

On such a branch, pin the commit of the tag. A tag name can be moved, so requirement files pin the commit SHA
(`a463422206485c26ddafc8d0873698854fd1ebef`, the merge commit of the version `0.1.0a1`):

```bash
pip install "tabrion @ git+https://github.com/peterkarlhuber/tabrion@a463422206485c26ddafc8d0873698854fd1ebef"
```

Master moved on to `0.1.0a2.dev0` right after that commit (TB-053), so a pin on a later master commit never looks
like the tag. Do not run `release.yml` for `v0.1.0a1`: its attest job would write a public build-provenance entry.

A library on such a branch declares a pre-release range, which pip needs to accept an alpha:

```toml
dependencies = ["tabrion>=0.1.0a1,<0.2"]
```

The rules:

- **Never on master.** A consumer's master carries only final ranges and pins (`tabrion>=0.1,<0.2`, plus a SHA pin
  of a final-version commit).
  The architecture check AC-6 (planned, QA-15) will refuse a pre-release specifier or pin there.
- A final range rejects the alpha: `tabrion>=0.1,<0.2` does not accept `0.1.0a1` (probe
  `proben/versionierung/`), because `0.1.0a1` is below the lower bound. This protects only against pre-releases of
  `0.1.0` itself: a pin on a later dev or pre-release commit inside the range (`0.1.1.dev0`, `0.1.1rc1`) is accepted
  by pip, and only AC-6 refuses it. A pre-release
  range `>=0.1.0a1,<0.2` accepts `0.1.0a1`, later pre-releases and `0.1.0`, but never `0.2.0a1`.
- One Tabrion version per installation (K-34): every package of an installation pins the same commit.
- The first merge of a consumer with a Tabrion edge needs `v0.1.0` final (TAB-040, the merge commit whose version
  is `0.1.0`); rebase the integration branch to the final range and the SHA pin of that commit then.
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
