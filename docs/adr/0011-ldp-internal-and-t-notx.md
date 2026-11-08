# ADR 0011: "Levuvo Database Profile" as an internal Tabrion test asset; contract rule T-NOTX

- **Status:** Accepted (2026-11-07; K-35, plan document 00 section 12.3). It replaces version 2, in which the
  standard with vendored vectors was the integration path.
- **Plan reference:** TAB-ADR-0011 (planning document 10)

## Context

- Tabrion's target behavior should be testable and versioned: profiles, transaction modes, role verdicts, error
  sanitizing, splitter.
- With K-30..K-32, Orkexa, Axentra and Nodestro use Tabrion directly. Vendored vectors in the consumers would be
  duplicate checks without benefit.
- Connections handed to Tabrion (the Axentra pool, Nodestro's `SharedPool`/M10, Orkexa's `ConnectionSource`) carry
  security-relevant state: `DbScope`, `row_factory`, open transactions.

## Decision

- **LDP stays internal to Tabrion:**
  - `conformance/SPEC.md` describes the observable target behavior with MUST and SHOULD and rule IDs.
  - `conformance/vectors/*.json` are machine-readable regression and behavior contracts: format `ldp-vectors/1`,
    `standard_version` `LDP-x.y.z`, `area`, vector IDs `LDP-XX-nnn`, JSON Schema draft 2020-12 (API contract
    section 10), `MANIFEST.json`, loader `tests/support/conformance.py`, CI job `conformance` (TAB-050).
- **No vendoring:** other repositories do not load the vectors. The field `vendored`, `vendor_check.py`, the SHA-256
  headers for copies and the drift check in the integrator are dropped. The former MIG-AXE-S* and MIG-ORK-1 parity is
  replaced by using the package.
- **Versioning:**
  - LDP SemVer is independent of the package version.
  - A changed expectation without a CHANGELOG entry fails CI.
  - MUST rules belong to the stable API (S-2, specification section 12).
- **Contract rule T-NOTX** (for every layer A function that takes a connection; API contract preamble):
  1. Tabrion never ends, commits or changes the transaction or session state of a connection it is given. This
     covers `commit`/`rollback` of foreign transactions, `autocommit`, `row_factory`, `SET`/`set_config`.
  2. The only exception: `configure` sets session parameters (timeouts, `application_name`, `TimeZone`,
     `search_path`), and only when the pool owner sets up the connection.
  3. If the connection is IDLE, Tabrion may open a short transaction **of its own** and ends it itself. If a foreign
     transaction is running, Tabrion's statements run inside it. Functions that need a transaction of their own
     raise `UsageError("...nested_transaction")`.
  4. Axentra never calls `configure` on a pool borrowed from Nodestro (`on_pool`, M10;
     [ADR 0013](0013-axentra-adopts-tabrion.md)).

## Rationale

- One implementation and one check in the same place.
- The vectors secure behavior across releases and are the basis for S-2 and S-6.
- T-NOTX is the technical precondition for the consumers' RLS, M10 and partition guarantees to stay unchanged when
  they use Tabrion.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| LDP as the integration path with vendored vectors (version 2) | Lifted by K-35; duplicate checks without benefit, drift machinery |
| Vectors as Python tests without a data format | Harder to version; no rule IDs for S-2 |
| Tabrion may "clean up" connections it is given (rollback, reset) | Destroys foreign transactions and scopes (RLS risk) |
| `configure` on every checkout | A session-wide change on foreign pools (M10) |

## Consequences

- Consumers check T-NOTX in their own contract tests, for example Axentra's `test_scope_survives_tabrion_calls`.
- `tabrion.testing` offers helpers for that, but no vectors.

## Security guarantees

- No Tabrion call can change a consumer's `DbScope`, open foreign transaction or row factory.
- Session-wide settings come only from the pool owner.

## Verification

- TAB-050: the schema validates; 100 % of the MUST and SHOULD vectors pass; every MUST rule has at least one vector.
- T-NOTX contract tests in Tabrion: `transaction_status`, `row_factory` and `current_setting(..., true)` are equal
  before and after every call, for every function with a connection parameter.
- MIG-T-AXE (G-AX-2, G-AX-5), TAB-T-160/161.

## Revision triggers

- An external implementer wants to use LDP; then it can be published as a standard of its own (G-R).

## Changes from the plan

None. The `conformance/` directory and the CI job `conformance` arrive with TAB-050 (M2).
