# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/)
with the 0.x rules of the specification (section 10) and ADR 0009.

## [Unreleased]

### Added

- Milestone M1 ("foundation"): package skeleton with the extras `postgres`, `sqlalchemy`, `migrations`, `testing`
  and `all`; ruff, mypy `--strict` and import-linter contracts (layers, stdlib-only core, no Levuvo platform).
- The complete public API of 0.1 and 0.2 as typed stubs (`__all__` in every module, every function raises
  `NotImplementedError`) and the API contract `docs/api/contract.md` for the API freeze G-T1.
- CI (`ci.yml`, `platforms.yml`, `ci-ok.yml` with `.github/ci-manifest.json`, `nightly.yml`) and a manual
  `release.yml` that builds and verifies but never publishes.
- Import isolation and the extras matrix: the core imports without psycopg, SQLAlchemy and Alembic.
- Documentation: specification, ADRs 0001 to 0013, risk register, program status, MkDocs site.
