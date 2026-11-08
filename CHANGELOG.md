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
- G-T1 follow-up: the API surface check `scripts/api_surface.py` with `docs/api/surface.json` (`since` and
  `stability` per name; CI job `api surface`); `Database.health(deep=...)`; the fixture `tabrion_role_setup`;
  `tabrion.testing.profiles.SQLiteConnect` and `PortabilityCase`; import contracts for layer B and
  `tabrion.migrations`; `ci-ok` posts its verdict as a commit status on the pull request head; the release
  attestation runs in its own job.
- Brand assets derived from the logo master (`assets/branding/`): lockups for light and dark surfaces, favicons and
  the social preview; used in the README and the documentation site.
- `TRADEMARKS.md`: the name and the logos are not covered by the MIT license.
- `ci-ok`: a pull request that changes `.github/workflows/**` or `.github/scripts/**` is NOT OK until the label
  `workflow-change-reviewed` is set; the status post is retried and fails loudly.

### Changed

- G-T1 follow-up: the 0.2 names (`Result`, `Column` and the 0.2 methods of `Database` and `Transaction`) are no
  longer exported before 0.2; `tabrion.testing.profiles.ConnectionFactory` is replaced by `SQLiteConnect`.
