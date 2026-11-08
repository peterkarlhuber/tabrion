# ADR 0004: Error model: classify in layer A, raise sanitized errors in layer B

- **Status:** Accepted (2026-11-07). The partial decision E-T03 (`fetch_one` raises on more than one row) took the
  recommended option.
- **Plan reference:** TAB-ADR-0004 (planning document 10)

## Context

- SQLAlchemy's `StatementError` contains `[parameters: ...]` (probes p.py, r.py).
- `str(psycopg.Error)` contains the `DETAIL` line with key values (B-06-1). Axentra's `redact_text` does not remove
  it, and Axentra logs it through `axentra/src/axentra/api/problems.py:57`.
- Nodestro embeds `{exc}` in `storage.migration_failed` and `backup_failed` (B-06-2: `storage.py:619, 632`;
  `storage_postgres.py:576, 601`).
- No platform has a central error classification (consolidation report 2.4).
- Axentra answers per RFC 9457 and never returns exception text in 5xx responses
  (`axentra/docs/adr/0009-errors-rfc9457.md:19`). The log is the gap.

## Decision

- **Layer A** classifies but never wraps driver errors in its own types:
  - `classify(exc) -> Classification(kind, sqlstate, constraint, retryable, outcome_unknown, ...)`.
  - `safe_message(exc) -> str` builds the text only from the SQLSTATE, the `Kind` and object names (constraint,
    table, column), without `DETAIL`, `HINT` values, parameters or paths. Error chains (`__cause__`/`__context__`)
    are resolved.
  - The driver exception reaches the caller unchanged.
- **Layer B** raises `TabrionError` subclasses (API contract section 1.4) with a stable `code`.
  - `str` and `repr` return the safe message.
  - The original is kept only in the attribute `original` and is **never** chained as `__cause__`
    (`raise ... from None`).
  - `statement` is cut to 200 characters and holds no parameters.
- **Drop-in rule:** the codes equal the Nodestro codes without the prefix `storage.`; Nodestro maps them by table to
  `NodestroError("storage.<suffix>")`.
- `classify` never raises.

## Rationale

- Nodestro keeps its exception types and `except` clauses. Axentra keeps its 16 local `except pg_errors.*` clauses
  (precise in their domain, category C, AX-9) and uses `safe_message`/`classify` only for logging (MIG-AXE-3,
  category B).
- New applications get a safe default hierarchy, and tracebacks in logs do not reveal parameters.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Layer A wraps every driver error in `TabrionError` | Breaks `except psycopg.errors.X` in existing code; large migration effort without benefit |
| Keep the `__cause__` chain | Standard logging (`logger.exception`) prints the chain with parameters and `DETAIL` |
| Regex redaction of the error text | Incomplete (languages, `lc_messages`); building the text from an allowlist is safer |
| Evaluate error texts through `lc_messages` | Language-dependent (R-T02); only the SQLSTATE is stable |

## Consequences

- Stage S2a: MIG-AXE-3 fixes B-06-1 through `tabrion.errors` plus a log filter; MIG-NOD-3 fixes B-06-2 in the SQLite
  and PostgreSQL paths through `safe_message`.
- MIG-AXE-0 (replaces E-06-6, owner decision 23:26Z): Axentra fixes B-06-1 right after G-A with a small, tested,
  local change that does not depend on Tabrion; MIG-AXE-3 later replaces the local fix with `tabrion.errors`, and the
  regression tests stay.
- TIMEOUT and CANCELED for SQLSTATE 57014 cannot always be told apart without context. Layer B knows its own
  cancellations; layer A reports CANCELED when in doubt (R-T02).

## Security guarantees

- No parameter value, no `DETAIL` value and no password from the conninfo appears in `str`, `repr`,
  `safe_message` or Tabrion's logs, at any log level.

## Verification

- TAB-T-E01..E09: mapping per SQLSTATE row against SQLite and PostgreSQL 16, 17 and 18.
- TAB-T-E10: canary value `SECRET-4711`, including the PostgreSQL `DETAIL` of a unique violation.
- TAB-T-E11: no `__cause__` chain.
- TAB-T-E12: the conninfo password does not appear.
- TAB-T-E13: `classify` never raises (Hypothesis).
- TAB-T-E14: `outcome_unknown` by fault injection.
- TAB-T-E15: `lc_messages=de_DE`.
- TAB-T-B18 (layer B) and QA-2 (canary suite in the consumers).

## Revision triggers

- New SQLSTATE classes in PostgreSQL 19.
- Studio needs structured error fields beyond `details`.

## Changes from the plan

- The plan does not specify the constructor of `TabrionError`. M1 proposes one in the stubs (`docs/api/contract.md`,
  open points); it is part of the G-T1 review.
