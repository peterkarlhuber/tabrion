# Contributing

Thank you for helping. Tabrion is a small library with strict rules about safety and dependencies; please read
this page before opening a pull request. Code, documentation, commits and ADRs are in US English.

## Development setup

Python 3.11 or later and pip (uv works as well, but is not required):

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Checks

Run everything CI runs before you push:

```bash
ruff check .
ruff format --check .
mypy --strict src tests
lint-imports                                   # import contracts (layers, stdlib-only core, no Levuvo platform)
pytest -m "not postgres and not packaging"     # the offline suite
pytest -m packaging tests/packaging            # wheel, sdist and the extras matrix in fresh venvs (slow)
mkdocs build --strict
```

### PostgreSQL tests

Tests with the marker `postgres` need a server. Start a throwaway one with Docker and point the suite at it:

```bash
docker run --rm -d --name tabrion-pg -e POSTGRES_PASSWORD=tabrion -p 5432:5432 postgres:17
export TABRION_TEST_POSTGRES="host=127.0.0.1 port=5432 user=postgres password=tabrion dbname=postgres"
pytest -m postgres
```

Without `TABRION_TEST_POSTGRES` these tests skip; `TABRION_REQUIRE_POSTGRES=1` turns the skip into a failure (CI
sets it). CI runs them against PostgreSQL 16, 17, 18 and `pgvector/pgvector:pg17`, with every image pinned by
digest.

## Rules for changes

- **Dependencies:** the core (`tabrion.errors`, `sql`, `config`, `sqlite`, `check`) uses the standard library only.
  `tabrion.postgres` may use psycopg, never SQLAlchemy or psycopg_pool. Tabrion never imports a Levuvo platform
  (orkexa, axentra, nodestro, tabrion-studio). `lint-imports` and `tests/architecture` enforce this. A new runtime
  dependency needs an ADR.
- **No values in errors or logs:** no parameter value, `DETAIL` line, password or path in a message.
- **Public API:** a change to `__all__`, a signature, a dataclass field or an error code updates
  `docs/api/contract.md` and `CHANGELOG.md` in the same pull request. Stable names change only after a deprecation
  window (specification section 10 and ADR 0009).
- **Tests first for fixes:** a bug fix comes with a test that fails without it. No new skips or xfails without a
  reason; no automatic re-runs.
- **CI:** actions are pinned by full commit SHA and container images by digest (`tests/security`).

## Pull requests and merging

- One work package per pull request where possible; name it (for example `TAB-011`) and list its acceptance
  criteria with evidence (test ID, CI job, SHA). The template does this.
- Update `CHANGELOG.md` (`Unreleased`), `docs/program/STATUS.md` and `docs/TODO.md`.
- Pull requests are merged with **merge commits** (no squash, no rebase), because pins in consuming repositories
  point at commits. The required check is `ci-ok`: merge only when its commit status is green (branch protection that
  enforces it is optional repository hardening).
- Releases: version bump by pull request, merged with a merge commit; consumers pin that commit by its full SHA
  (`git+https`). An annotated tag `vX.Y.Z` on the merge commit is an optional label; tags never move. Publishing
  anything (PyPI, npm, GitHub release, announcement) needs the owner's approval G-R for that version.
