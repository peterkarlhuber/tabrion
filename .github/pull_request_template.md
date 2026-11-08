## Summary

<!-- What does this change and why? Name the work package (TAB-0xx, QA-xx) and link the issue if there is one. -->

## Changes

-

## Acceptance criteria and evidence

<!-- One line per criterion of the work package: test ID, CI job or SHA. -->

## Testing

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy --strict src tests`
- [ ] `lint-imports`
- [ ] `pytest -m "not postgres and not packaging"` (and `-m postgres` with `TABRION_TEST_POSTGRES`, if PostgreSQL code changed)
- [ ] `mkdocs build --strict` (if docs changed)
- [ ] `pytest -m packaging tests/packaging` (if packaging changed)

## Checklist

- [ ] Tests cover the change (a bug fix has a test that fails without it)
- [ ] `CHANGELOG.md` updated (`Unreleased`); public API changes also in `docs/api/contract.md`
- [ ] No parameter value, password or other secret in a new error or log path
- [ ] `docs/program/STATUS.md` and `docs/TODO.md` updated
