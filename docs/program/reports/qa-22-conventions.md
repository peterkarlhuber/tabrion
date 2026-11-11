# QA-22: conventions audit of the new repositories

Work package TABRION QA-22 (planning document 09, M2; planning document 05: compare the new repositories with orkexa
and axentra). Acceptance: the checklist "Actions pins, ci-manifest, Dependabot, branch protection, English, license
file, SECURITY.md" with every item met and an evidence link per item.

Audited on 2026-11-11: tabrion master `cf815fd` and tabrion-studio master `061db9d`, compared with orkexa master
`840add4` and axentra master `ea7aa9f`. The Studio side is also recorded in its repository
(`docs/development/conventions.md`, tabrion-studio #28).

## Checklist

| Item | tabrion | tabrion-studio | Evidence |
|---|---|---|---|
| Actions pinned by full commit SHA, with the version as a comment; container images by digest | **Met** | **Met** | tabrion: [`test_workflow_pins.py`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/tests/security/test_workflow_pins.py) (TAB-T-181, with a negative probe), CI job `security tests`; Studio: [`test_workflows.py`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/tests/security/test_workflows.py). No workflow of either repository uses a tag or an image without a digest (checked again by hand) |
| `ci-manifest.json` and the `ci-ok` gate | **Met** | **Met** | [tabrion manifest](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/.github/ci-manifest.json) and [`ci-ok.yml`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/.github/workflows/ci-ok.yml), [`test_ci_ok.py`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/.github/scripts/test_ci_ok.py); [Studio manifest](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/ci-manifest.json), [`ci-ok.yml`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/workflows/ci-ok.yml). New: `tests/security/test_conventions.py::test_ci_manifest_names_existing_workflows` |
| Dependabot | **Met** | **Met** | [tabrion](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/.github/dependabot.yml): pip and github-actions, weekly, cooldown, majors of psycopg/SQLAlchemy/Alembic by hand, no auto-merge; [Studio](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/dependabot.yml): pip, npm (`/frontend`) and github-actions, the same policy. New: `test_dependabot_covers_pip_and_actions_weekly_with_a_cooldown` |
| Branch protection | **Optional by owner decision** (D-84) | **Optional by owner decision** (D-84) | Owner decision of 2026-11-08 11:26Z (deviation D-67, TODO [TB-003](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/docs/TODO.md)): branch protection, the required check `ci-ok` and private vulnerability reporting are optional hardening. The merge rule "`ci-ok` green on the head SHA" is in [CONTRIBUTING.md](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/CONTRIBUTING.md); merges are merge commits only. The settings cannot be read with the agent's tools (no admin API); the owner sees them under Settings, Branches |
| English | **Met** | **Met** | A scan of every tracked text file for German words and umlauts finds only test data (a localized PostgreSQL message in [`test_errors.py`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/tests/unit/test_errors.py), non-ASCII identifiers and keys) in tabrion, and in Studio only the German UI locale ([`de.json`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/frontend/src/i18n/locales/de.json)), its tests and the bilingual `<noscript>` line: product content, not repository text |
| License file | **Met** | **Met** | [tabrion `LICENSE`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/LICENSE) (MIT), `license = "MIT"` and `license-files` in [`pyproject.toml`](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/pyproject.toml); [Studio `LICENSE`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/LICENSE) (MIT, plus `TRADEMARKS.md` in `license-files`). New: `test_license_is_mit_in_the_file_and_the_metadata` |
| `SECURITY.md` | **Met** | **Met** | [tabrion](https://github.com/peterkarlhuber/tabrion/blob/cf815fd41414e6d286ad73ffd13fbfb9c9f98552/SECURITY.md) and [Studio](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/SECURITY.md): supported versions, private reporting (private vulnerability reporting if enabled, else a detail-free contact request). New: `test_security_policy_names_a_private_reporting_route` |

All items are met; branch protection is met in the form the owner decided (optional, D-84).

## Comparison with orkexa and axentra

| Convention | orkexa | axentra | tabrion | tabrion-studio |
|---|---|---|---|---|
| `ci-ok` with `ci-manifest.json` | yes | yes | yes | yes |
| `platforms.yml` (macOS, Windows) | yes | no | yes | no (OS matrix inside `ci.yml`) |
| `release.yml` | yes | no | yes | no (no release before G-R) |
| Dependabot | yes | yes | yes | yes (plus npm) |
| `SECURITY.md`, `CONTRIBUTING.md` | yes | no | yes | yes |
| `CODE_OF_CONDUCT.md`, `SUPPORT.md` | yes | no | yes | no |
| Issue templates, PR template | yes | no | yes | yes |
| `CODEOWNERS` | no | no | no | yes (`/.github/`) |
| `.gitattributes` | yes (byte-exact fixtures) | no | no | yes |
| `.editorconfig` | yes | no | no | no |
| `NOTICE` | yes | yes (`NOTICE.md`) | no (`TRADEMARKS.md`) | no (`TRADEMARKS.md`) |

The new repositories follow orkexa, the more complete of the two; axentra has fewer of these files and is not the
benchmark.

## Findings (not part of the checklist)

- **F-1 (Low): tabrion has no `.gitattributes` and no `.editorconfig`.** orkexa pins line endings for byte-compared
  fixtures; tabrion compares golden files as JSON, not bytes, and the Windows job is green on them, so nothing breaks
  today. Recorded in TODO TB-009.
- **F-2 (Low): Studio had no `CODE_OF_CONDUCT.md` and no `SUPPORT.md`.** Added in the Studio QA-22 pull request
  (tabrion-studio #28), which also adds its `test_conventions.py`.
- **F-3 (Info): tabrion has no `CODEOWNERS`.** Studio has one for `/.github/` (review F-03 there); TB-003 already
  recommends it for both repositories, as part of the optional hardening.

## What keeps this true

`tests/security/test_conventions.py` (CI job `security tests`) checks the license, the security policy, the community
files, Dependabot and the manifest; `test_workflow_pins.py` checks the pins. Branch protection stays an owner setting.
