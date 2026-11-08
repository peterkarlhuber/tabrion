# ADR 0006: PostgreSQL session profile, R-SET, R-NORETRY

- **Status:** Accepted (2026-11-07). It depended on **E-T02** (the strict TLS rule `auto`); the recommended option
  was taken.
- **Plan reference:** TAB-ADR-0006 (planning document 10)

## Context

- Probe r.py:
  - `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` default to 0.
  - `application_name` is empty.
  - SQLAlchemy uses QueuePool 5+10 and `pre_ping=False`.
- Nodestro sets 30 s / 300 s (`storage_postgres.py:279`), Axentra 30 s (`database.py:274`). No platform sets
  `lock_timeout` or `application_name`.
- F-02: repeating write statements after a lost reply leads to lost jobs and false reports (fault injection,
  `proben/jobqueue_retry_probe.py`).
- s.py: `set_config(..., true)` is transaction-local and empty after the transaction.

## Decision

- **`SessionSettings`** (specification section 4.2):

  | Setting | Default |
  |---|---|
  | `statement_timeout` | 30 000 ms |
  | `lock_timeout` | 10 000 ms |
  | `idle_in_transaction_session_timeout` | 300 000 ms |
  | `transaction_timeout` | `None`; effective from PostgreSQL 17 only, skipped on 16 (N-08) |
  | `application_name` | required in layer B, default `"tabrion"` |
  | `TimeZone` | `UTC` |
  | `search_path` | unchanged unless set explicitly (quoted) |

  `configure()` sets every value in **one** `set_config` round trip with `is_local=false`. On a non-autocommit
  connection without an open transaction a `commit()` follows; an open foreign transaction is never ended.
- **TLS (`Profile.tls="auto"`, E-T02):**
  - For loopback and Unix sockets the URL stays unchanged.
  - For remote hosts `sslmode=require` is set when it is missing. `disable`, `allow` and `prefer` raise
    `ConfigurationError`.
  - `tabrion.check` reports `TAB-PG-010` as long as `verify-full` is not in force.
  - Layer A enforces nothing on foreign connections; it only diagnoses.
- **R-SET:**
  - Tabrion never sets tenant or security settings.
  - Consumers set them only transaction-locally (`set_config(name, value, true)` / `SET LOCAL`).
  - Session-wide, `configure()` sets only timeouts, `application_name`, `TimeZone` and `search_path`; never `role`
    or `row_security`.
  - Connections go back to the pool only in state IDLE; otherwise they are discarded.
- **R-NORETRY:**
  - Tabrion never repeats a statement whose sending has started, in neither layer.
  - Retries happen only while connecting or in the checkout check (`pool_pre_ping`), **before** sending.
  - An error after sending gives `Kind.CONNECTION` with `outcome_unknown=True` and is passed up.
- **Layer B pool:** `pool_size=5`, `max_overflow=0`, `pool_timeout=10 s`, `pool_pre_ping=True`,
  `pool_recycle=1800 s`.

## Rationale

- Unlimited timeouts and missing attribution (`application_name`) are the most common operational cause of hanging
  systems.
- R-SET protects consumers with a transaction-local tenant context (Nodestro's `DatabaseSource.tenant_setting`,
  Axentra's `DbScope`) from settings that travel across pooled connections (TM-08, AX-I-1..3).
- R-NORETRY rules out the error class of F-02 at the library level.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Take the driver or SQLAlchemy defaults | Proven unsafe (r.py) |
| Automatic retry of idempotent reads after sending | Idempotency cannot be derived safely from SQL text; an exception would dilute the rule (contradiction W-5) |
| Tolerate `sslmode=prefer` | Allows a silent downgrade |
| Session scope (`SET app.tenant`) as a Tabrion function | The tenant model does not belong in Tabrion ([ADR 0010](0010-model-neutrality.md)); RLS stays with Axentra |

## Consequences

- Nodestro delegates `_configure` to `configure()` (MIG-NOD-4 and -8); the values stay the same, and
  `lock_timeout`/`application_name` are not set by default.
- Axentra delegates `_configure` to `tabrion.postgres.configure` (MIG-AXE-4, acceptance: an empty `SHOW ALL` diff).
  On a pool borrowed from Nodestro (`on_pool`, M10) Axentra **never** calls `configure` (T-NOTX,
  [ADR 0011](0011-ldp-internal-and-t-notx.md), [ADR 0013](0013-axentra-adopts-tabrion.md)).
- A per-statement timeout in layer B uses `SET LOCAL` and is reset before the next statement (R-T06).

## Security guarantees

- No session-wide security settings.
- No TLS downgrade against remote hosts in the default profile.
- No hidden double write by Tabrion.

## Verification

- TAB-T-P01..P05: `configure` (values, commit rule, open transaction, one round trip).
- TAB-T-P06..P09: `ping`.
- TAB-T-160: a transaction-local setting is empty after the transaction.
- TAB-T-161: a connection with an open transaction is never reused.
- TAB-T-170: fault injection; the statement runs exactly once.
- TAB-T-009: database restart.
- TAB-T-B13..B14: timeouts. TAB-T-B15: cancel.
- JQ-T-1..4 (existing).

## Revision triggers

- PostgreSQL 19 changes the timeout GUCs.
- A consumer needs `target_session_attrs` or failover configuration.

## Changes from the plan

None in content. A note on contradiction W-7: R-NORETRY is read as plan document 00 section 10 W-5 says. Tabrion itself never retries; only users may retry pure read
  statements outside transactions (RT2-03).
