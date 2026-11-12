# QA-8: supply chain (tabrion)

Work package TABRION QA-8 (planning document 09; threat TM-10 of planning document 07). Acceptance: pip-audit (and
npm audit, for tabrion-studio) without a High finding; a CycloneDX SBOM per build; two builds with the same wheel
hash. This report covers `peterkarlhuber/tabrion`; tabrion-studio has its own report in its repository
(`docs/development/supply-chain.md`).

Checked on 2026-11-08 at master `07fe9e3` plus this pull request; review round of PR #30 on master `4d8e486`.

| Criterion | Result | Evidence |
|---|---|---|
| pip-audit without High | **Met.** No known vulnerability in the core, every extra and the development toolchain. The CI job fails on **any** known vulnerability, which is stricter than "no High" (pip-audit has no severity filter). | CI job `dependency audit` (`pip-audit==2.10.1 --skip-editable` over `.[dev]`, on every pull request, required through `ci-ok`); local run: "No known vulnerabilities found" |
| npm audit | Not applicable: tabrion has no JavaScript. | - |
| SBOM (CycloneDX) per build | **Met in code and tests; the CI step is a workflow change (TODO TB-130).** `scripts/sbom.py` (standard library only) writes a CycloneDX 1.6 JSON SBOM for a built wheel: the wheel with its SHA-256, license and repository; the build backend that wrote it (`metadata.tools`, from the wheel's `WHEEL` generator); every declared dependency with the extras that pull it in, its version range and the extras it requests (`psycopg[binary]`), `scope: required` for a core dependency and `optional` behind an extra (the core needs nothing, TAB-T-180); with `--environment`, the resolved versions of an installation, a VCS install (PEP 610 `direct_url.json`) as `pkg:generic` with its commit and every other one as `pkg:pypi`. Package URLs in canonical encoding; `--vcs-ref` must be a full commit SHA. Deterministic output (sorted, serial number derived from the canonical document, timestamp only from `SOURCE_DATE_EPOCH`, which must be an integer). Validated against the CycloneDX 1.6 schema with `cyclonedx-python-lib` (a scratch environment, not a dependency). | `scripts/sbom.py`; `tests/unit/test_sbom.py` (shape, backend, serial numbers, refused refs and epochs, dependencies, required core, environment and VCS installs, determinism, a broken requirement); `tests/packaging/test_artifacts.py::test_sbom_of_the_wheel` on the wheel of the CI job `build and install` |
| Two builds, same wheel hash | **Met, for the pinned build backend.** Hatchling writes reproducible wheels (fixed timestamps and order), but its `WHEEL` file names the generator version, so the hash holds only for one backend version. `pyproject.toml` pins `hatchling==1.33.0`, and `build-constraints.txt` pins it and its dependencies with SHA-256 hashes (`PIP_CONSTRAINT=build-constraints.txt PIP_REQUIRE_HASHES=1`; a changed hash is refused). Two fresh builds give the same SHA-256, and equal the wheel of the CI build job; two builds with the same `SOURCE_DATE_EPOCH` match each other. The build runs offline from a local wheel cache (`PIP_NO_INDEX=1 PIP_FIND_LINKS=wheelhouse`, recipe in `build-constraints.txt`). Scope: same commit, same backend pins, any machine and time; not across backend versions. | `tests/packaging/test_artifacts.py::test_two_builds_give_the_same_wheel_hash` and `::test_two_builds_with_source_date_epoch_match` (job `build and install`); `tests/unit/test_build_constraints.py` (both pins agree, every pin hashed); local: two builds `38c4639f...6708602` each, also offline with hashes required |
| Actions pinned by SHA | Met (TAB-T-181). | `tests/security/test_workflow_pins.py` |
| Dependabot | Met: weekly grouped updates for pip and GitHub Actions, cooldown, no auto-merge, majors of psycopg, SQLAlchemy and Alembic by hand. | `.github/dependabot.yml` |
| Minimal runtime dependencies | Met: the core wheel has no `Requires-Dist` outside an extra. | `tests/packaging/test_artifacts.py::test_core_wheel_requires_nothing` (TAB-T-180) |

## Usage

```bash
PIP_CONSTRAINT=build-constraints.txt PIP_REQUIRE_HASHES=1 python -m build --outdir dist
mkdir -p sbom
python scripts/sbom.py dist/tabrion-*.whl --vcs-ref "$(git rev-parse HEAD)" -o sbom/tabrion.cdx.json
```

The SBOM goes outside `dist/`, so `twine check dist/*` sees only the wheel and the sdist.

Add `--environment` in an installation to record the exact versions that were installed with Tabrion.

## Open

- TB-130: build with the hashed constraints and generate the SBOM in the CI jobs and in `release.yml`, and attest it
  with the build provenance (a `.github/` change, reviewed separately; the exact steps are in the TODO entry).
- TB-133: check that Dependabot proposes updates of the two backend pins together, and keep them in step by hand
  until it does.
- Tabrion is consumed through Git SHA pins, not PyPI (ADR 0009); the SBOM's package URL is therefore `pkg:generic`
  with a `vcs_url` qualifier.
