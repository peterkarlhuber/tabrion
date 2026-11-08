# QA-8: supply chain (tabrion)

Work package TABRION QA-8 (planning document 09; threat TM-10 of planning document 07). Acceptance: pip-audit (and
npm audit, for tabrion-studio) without a High finding; a CycloneDX SBOM per build; two builds with the same wheel
hash. This report covers `peterkarlhuber/tabrion`; tabrion-studio has its own report in its repository
(`docs/development/supply-chain.md`).

Checked on 2026-11-08 at master `07fe9e3` plus this pull request.

| Criterion | Result | Evidence |
|---|---|---|
| pip-audit without High | **Met.** No known vulnerability in the core, every extra and the development toolchain. The CI job fails on **any** known vulnerability, which is stricter than "no High" (pip-audit has no severity filter). | CI job `dependency audit` (`pip-audit==2.10.1 --skip-editable` over `.[dev]`, on every pull request, required through `ci-ok`); local run: "No known vulnerabilities found" |
| npm audit | Not applicable: tabrion has no JavaScript. | - |
| SBOM (CycloneDX) per build | **Met in code and tests; the CI step is a workflow change (TODO TB-130).** `scripts/sbom.py` (standard library only) writes a CycloneDX 1.6 JSON SBOM for a built wheel: the wheel with its SHA-256, license and repository; every declared dependency with the extras that pull it in and its version range (`scope: optional`, the core needs nothing, TAB-T-180); with `--environment`, the resolved versions of an installation. Deterministic output (sorted, serial number derived from the wheel hash, timestamp only from `SOURCE_DATE_EPOCH`). | `scripts/sbom.py`; `tests/unit/test_sbom.py` (shape, dependencies, environment, determinism, a broken requirement); `tests/packaging/test_artifacts.py::test_sbom_of_the_wheel` on the wheel of the CI job `build and install` |
| Two builds, same wheel hash | **Met.** Hatchling writes reproducible wheels (fixed timestamps and order). Two fresh builds give the same SHA-256, with and without `SOURCE_DATE_EPOCH`, and equal the wheel of the CI build job. | `tests/packaging/test_artifacts.py::test_two_builds_give_the_same_wheel_hash` (job `build and install`); local: two builds `38c4639f...6708602` each |
| Actions pinned by SHA | Met (TAB-T-181). | `tests/security/test_workflow_pins.py` |
| Dependabot | Met: weekly grouped updates for pip and GitHub Actions, cooldown, no auto-merge, majors of psycopg, SQLAlchemy and Alembic by hand. | `.github/dependabot.yml` |
| Minimal runtime dependencies | Met: the core wheel has no `Requires-Dist` outside an extra. | `tests/packaging/test_artifacts.py::test_core_wheel_requires_nothing` (TAB-T-180) |

## Usage

```bash
python -m build --outdir dist
python scripts/sbom.py dist/tabrion-*.whl --vcs-ref "$(git rev-parse HEAD)" -o dist/tabrion.cdx.json
```

Add `--environment` in an installation to record the exact versions that were installed with Tabrion.

## Open

- TB-130: generate the SBOM in the CI job `build and install` and in `release.yml` next to the wheel, and attest it
  with the build provenance (a `.github/` change, reviewed separately).
- Tabrion is consumed through Git SHA pins, not PyPI (ADR 0009); the SBOM's package URL is therefore `pkg:generic`
  with a `vcs_url` qualifier.
