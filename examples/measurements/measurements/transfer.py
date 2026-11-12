"""Transfer: send the captured readings upstream in batches, idempotently (TABRION RA-B-2).

The rules (planning document 09 section 7, the lesson of F-02: retry only what is idempotent):

1. Batches follow the local high-water mark: the rows with ``seq`` above ``Store.mark``, in capture order.
2. Each batch is one upstream transaction with one ``INSERT ... ON CONFLICT (device_id, channel, ts_utc) DO
   NOTHING``. A row that is already upstream is skipped, so sending a batch again changes nothing.
3. The mark moves only after the upstream ``COMMIT`` returned. Whatever fails before that (the network, the server,
   the process), the mark still points before the batch, and the next run sends it again; when the ``COMMIT`` did
   reach the server but its answer was lost, rule 2 turns the second copy into a no-op.

So a run can stop anywhere and be repeated as often as needed: nothing is lost and nothing is stored twice. The
local capture never waits for the upstream: the transfer reads the local file in short read transactions and holds
no local lock during the upstream round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from measurements.store import DEFAULT_TARGET, Store, Stored
from measurements.upstream import MAX_BATCH, insert_new
from tabrion import Database
from tabrion.errors import TabrionError

UpstreamError = TabrionError
"""What ``transfer`` raises when the upstream fails: Tabrion's value-free error with its classification."""


@dataclass(frozen=True, slots=True)
class Report:
    batches: int
    """Batches committed upstream in this run."""
    sent: int
    """Rows in those batches."""
    inserted: int
    """Rows that were new upstream; ``sent - inserted`` were already there (sent before, answer lost)."""
    mark: int
    """The high-water mark after the run."""


def _row(stored: Stored) -> dict[str, Any]:
    measurement = stored.measurement
    return {
        "device_id": measurement.device_id,
        "channel": measurement.channel,
        "ts_utc": measurement.ts_utc,  # aware, UTC
        "value": measurement.value,
        "unit": measurement.unit,
        "quality": measurement.quality,
        "source_seq": stored.seq,
    }


def transfer(
    store: Store,
    upstream: Database,
    *,
    target: str = DEFAULT_TARGET,
    batch_size: int = 1000,
    max_batches: int | None = None,
    timeout: float | None = None,
) -> Report:
    """Send every row above the mark of ``target`` (or ``max_batches`` batches of them) to ``upstream``.

    An upstream error (a ``tabrion.TabrionError``: connection lost, timeout, server error) propagates unchanged; the
    batches committed before it keep their mark, the failed one is sent again by the next run. ``timeout`` is the
    statement timeout in seconds.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError(f"batch_size must be an int from 1 to {MAX_BATCH}")
    mark = store.mark(target)
    batches = sent = inserted = 0
    while max_batches is None or batches < max_batches:
        page = store.after(mark, limit=batch_size)
        if not page:
            break
        statement = insert_new(upstream.dialect, [_row(stored) for stored in page])
        with upstream.transaction(timeout=timeout) as transaction:
            new = len(transaction.fetch_all(statement))
        # Here the upstream COMMIT has returned, and only here does the mark move (rule 3).
        mark = page[-1].seq
        store.advance(target, mark)
        batches += 1
        sent += len(page)
        inserted += new
    return Report(batches=batches, sent=sent, inserted=inserted, mark=mark)
