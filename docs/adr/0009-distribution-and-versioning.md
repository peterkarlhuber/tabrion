# ADR 0009: Distribution and versioning under diamond dependencies

- **Status:** Accepted (2026-11-07). Owner decisions of 2026-11-07 23:26Z (plan document 00 section 13):
  - **E-VER:** V-B0 as the transition, V-B (PyPI) as the goal, V-A as the fallback;
  - **E-PyPI:** no standing approval; every public release (the first PyPI version and pre-releases included) needs
    G-R; TAB-043 is bound to G-R;
  - **E-Vis:** public repository.
- **Amended:** 2026-11-08 by the owner decision of 2026-11-08 11:26Z, see
  [Amendment 2026-11-08](#amendment-2026-11-08-git-pin-distribution).
- **Plan reference:** TAB-ADR-0009 (planning document 10)

## Context

- The diamond: nodestro-runtime to orkexa to tabrion (core and `[postgres]`), runtime to axentra to
  tabrion[postgres], runtime to tabrion. There may be exactly **one** Tabrion version per installation (K-34).
- pip probe (plan document 06 section 6.2, pip 26.2.2):
  - A direct URL of the application satisfies a library's range.
  - Two different direct URLs of the same package give `ResolutionImpossible`.
- Levuvo pins by full Git SHA today (orkexa without tags), with merge commits and `check_versions.py`.

## Decision

| Variant | orkexa, axentra | nodestro-runtime / -api | When |
|---|---|---|---|
| **V-B0** (now) | **range** `tabrion[...]>=0.N,<0.(N+1)` in `dependencies` and extras; standalone installation and CI through `requirements/tabrion.txt` (one direct-URL pin to a final tag SHA, installed with `-r`, not `-c`; at least pip 25.0, CI with the minimum and the current pip) | a single Git SHA pin (runtime extras and `dev`, api `dev` with the identical SHA) | from the start of phase B |
| **V-B** | range as in V-B0, CI from PyPI | `tabrion==0.N.M` as a lock with hash | only after G-R (TAB-043; every public version separately) |
| **V-A** (fallback) | identical tag SHA pin everywhere (rule P-1) | same SHA | only if V-B0 fails |

**Release coordination RC-1..RC-8 (plan document 06 section 6.4):**

| Rule | Content |
|---|---|
| RC-1 | Libraries never reference Tabrion by direct URL in `dependencies` or extras; AC-2 checks this |
| RC-2 | Exactly one Tabrion source in the Nodestro chain: the SHA in runtime and api `dev` is the same; the range of the pinned orkexa and axentra commits contains that version; checked by `check_versions.py`/AC-6 |
| RC-3 | 0.x: a patch is compatible only, a minor may break (CHANGELOG "Breaking"); full SemVer from 1.0 |
| RC-4 | A range is raised only after the library's CI was green against the new minor |
| RC-5 | One thread at a time leads a Tabrion wave; the other threads are told before every pull request and after every merge |
| RC-6 | A wave that changes the Tabrion core needs green desktop builds on three operating systems and a green blocked-module test |
| RC-7 | Nightly check of `requirements/tabrion.txt` (orkexa, axentra) against the runtime SHA, not blocking |
| RC-8 | Ranges on master apply to final versions only (pip rejects `0.1.0a1`, `0.1.0rc1`, `0.2.0a1` against `>=0.1,<0.2`; probe `proben/versionierung/ERGEBNIS.md`); pre-release ranges only on unmerged integration branches; no pre-release specifier or pin on master of orkexa, axentra or nodestro (AC-6); the first merges with a Tabrion edge require `v0.1.0` final |

**No conflicting pins:**

1. Exactly one Tabrion version per installation (AC-6, AC-4).
2. Pin and requirements files reference final tags only.
3. At most one Tabrion pin per repository (runtime extras equal api `dev`).
4. Nodestro's pin governs Nodestro installations; a library's `requirements/tabrion.txt` may carry an older final
   tag of the same minor (a notice, not an error).

**Cascade:**

- Patch (V-B0): tabrion tag, then nodestro-runtime, api, sdk, studio (`platform/versions.json` with the component
  `tabrion`), then server, local-docker and desktop. orkexa and axentra stay unchanged.
- Minor: first orkexa and axentra in parallel (raise the range per RC-4), then runtime with new orkexa and axentra
  pins, then api, sdk, studio and the builds.

**API stability policy (specification section 12, S-1..S-8):**

| Rule | Content |
|---|---|
| S-1 | SemVer 0.x as in RC-3 |
| S-2 | Stable are `__all__` entries with status S, error codes, dataclass fields, `Kind` values and the MUST rules of `SPEC.md`. Consumer libraries must not use experimental APIs |
| S-3 | Deprecation window: removal at the earliest in the minor after next and at least 8 weeks after the deprecation release; consumer CI runs with `-W error::DeprecationWarning` |
| S-4 | Additive changes are not breaking |
| S-5 | Security tightening is allowed in a patch (CHANGELOG `Security`) |
| S-6 | API surface check `scripts/api_surface.py` against `docs/api/surface.json` |
| S-7 | The downstream job (AC-5) blocks the next Tabrion release while red |
| S-8 | A breaking cascade follows the order of the edge list |

**Tags:** annotated `vX.Y.Z`, only on master merge commits, never moved. Without G-R, tags, pins and internal
pre-releases (`0.1.0rc1`) are allowed. GitHub releases, PyPI and announcements come only after G-R.

**Version cut:**

- **0.1:** layer A complete, including `SessionLock`, the advisory helpers, the role functions and
  `grant_runtime_role`, because Axentra and Nodestro need them (plan document 00 section 12.3).
- **0.2:** the Studio APIs (`query`/`Result`, `cancel`, `inspector`).
- **1.0:** only after the Axentra and Nodestro integration is complete, Orkexa per E-ORK-CORE, four weeks of
  reference applications without an API break, a positive review and G-R for that release.

## Rationale

- V-B0 avoids `ResolutionImpossible` in the diamond without bringing the PyPI release forward.
- A Tabrion patch forces no commit in orkexa or axentra: about 6 instead of 9 pull requests per wave; about 10
  instead of about 15 agent days for ten bumps (estimate).
- V-B is the usual Python pattern (libraries declare ranges, applications pin).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| V-A as the default (identical SHA everywhere, P-1) | Every Tabrion change forces pin pull requests in orkexa and axentra; a forgotten pin breaks the installation; fallback only |
| PyPI right away (V-B without a transition) | Needs G-R, GOV-1 and the name rights first |
| Exact versions (`==`) in libraries | Creates the same conflict as V-A |
| Moving tags (`latest`) | Not reproducible |

## Consequences

- Under V-B0, axentra and orkexa install standalone and reproducibly through
  `pip install -r requirements/tabrion.txt "<lib> @ git+...@<sha>"` (documented in their README; AC-4). Library CI
  may test against a different patch version than Nodestro (R-07); RC-7 and AC-5 catch that.
- `check_versions.py` gets the component `tabrion` and range and cycle checks (AC-6, QA-15).

## Security guarantees

- Reproducible application installs (SHA or hash constraint).
- Security patches reach every consumer without a library release, because the range already allows them.
- No release without G-R. `NODESTRO_READ` stays untouched.

## Verification

- AC-6 with negative tests (two SHAs, an empty range intersection, a cycle).
- AC-4: the clean-install matrix (plan document 00 section 13), including standalone through
  `requirements/tabrion.txt` and negative cases (two pins, a pre-release against a final range, a 0.(N+1) alpha);
  pip 25.0 and current.
- S-6: API surface check.
- AC-5: consumer compat.
- QA-11: the release workflow without publishing. In M1 the workflow `.github/workflows/release.yml` builds and
  attests only; its publish job is gated by the protected environment `pypi` and uploads nothing.

### Release environment (G-T1 condition C-8)

The YAML alone proves no approval: GitHub creates a missing environment unprotected on its first use, and a user
with write access can dispatch `release.yml` from a branch with an edited copy. The protection therefore lives in
repository settings that the owner sets before TAB-043 (owner action O-2 in `docs/program/STATUS.md`):

| Setting | Value | Why |
|---|---|---|
| Environment | `pypi` exists before the first dispatch | otherwise it is created without rules |
| Required reviewers | the owner (Peter) | G-R per version |
| Prevent self-review | on | the person who dispatches cannot approve the deployment |
| Deployment branches and tags | "Selected branches and tags", one tag rule `v*`, no branch rule | a run from a branch (an edited copy of the workflow) cannot deploy to `pypi` |
| Environment secrets | none | publishing uses OIDC (trusted publishing), not a token |
| PyPI trusted publisher (TAB-043) | owner `peterkarlhuber`, repository `tabrion`, workflow `release.yml`, environment `pypi` | PyPI accepts an OIDC token only from an approved job of that environment |

In the workflow, only the small job `attest` holds `id-token: write`; it neither checks out code nor installs
packages. The job `build`, which runs pip and the packaging tests, has `contents: read` only, so third-party code in
it can never mint an OIDC token, whatever the trusted publisher is bound to.

## Revision triggers

- G-R for the first PyPI version (transition from V-B0 to V-B).
- An external consumer joins.
- Tabrion 1.0.

## Amendment 2026-11-08: Git-pin distribution

Owner decision of 2026-11-08 11:26Z (plan document 00 section 13). The decision text above stays as it was accepted;
where it conflicts with this section, this section applies.

- **No PyPI publication for now.** The consumers (Nodestro, Axentra, Orkexa, Tabrion Studio) take Tabrion through
  reproducible Git SHA pins (`tabrion @ git+https://github.com/peterkarlhuber/tabrion@<full commit sha>`) or
  internal packages. V-B0 is the operating mode without a planned end date; V-B (PyPI) comes only if a publication
  is decided later, and then with G-R for every version (E-PyPI unchanged). TAB-043 stays bound to G-R and is not
  planned. The environment `pypi` ([Release environment](#release-environment-g-t1-condition-c-8)) is needed only
  then.
- **Tags are optional labels.** The agent may create an annotated tag `vX.Y.Z` on a master merge commit; a tag is
  never moved and there is no GitHub release without G-R. Pins always name the full commit SHA, never a tag name, so
  nothing waits on a tag (the tag `v0.1.0a1` on `a463422` exists in the agent's clone only, because the agent's Git
  proxy refuses tag pushes).
- **`v0.1.0` final (TAB-040)** is the master merge commit whose package version is `0.1.0` (not a pre-release).
  Consumers pin it by its full commit SHA via `git+https`; a tag `v0.1.0` on it is an optional label.
- **RC-8 holds unchanged:** master of a consumer carries only final ranges such as `tabrion>=0.1,<0.2`, plus a SHA pin
  of a final-version commit (a commit whose package version is a final version). It no longer waits on PyPI.
  "Final tag" in V-B0, rule 2 and rule 4 of "No conflicting pins" and the cascade reads "final-version commit, named
  by its full SHA".
- **CI of consumers** installs Tabrion from the Git SHA pin (the requirements file `requirements/tabrion.txt` with
  `-r`, or the application's own pin), not from PyPI.
- **Unchanged:** every public publication (PyPI, npm, GitHub release, announcement) needs G-R per version; the human
  security review before a public Tabrion Studio release stays; all security, test and quality requirements stay.
- **Repository hardening** (branch protection, private vulnerability reporting, the social preview) is optional and
  never a prerequisite for implementation; gate G-T2 no longer requires a trademark register check (ADR 0001).

## Changes from the plan

- The plan's release trigger is a published GitHub release. Because GitHub releases need G-R, the workflow runs only
  by manual dispatch (`workflow_dispatch`) and the publish job needs the protected environment `pypi`, which the
  owner has to create with a required reviewer. Until TAB-043 the publish job fails on purpose.
