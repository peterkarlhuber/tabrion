"""Measurements reference application, part 1: local capture on SQLite (TABRION RA-B-1).

Layer A only (``tabrion.sqlite``, standard library): the capture side of an edge device. RA-B-2 adds the transfer
to PostgreSQL through layer B; the natural key ``(device_id, channel, ts_utc)`` and the local sequence ``seq`` are
already here for it.
"""

from measurements.db import SCHEMA_VERSION, open_database
from measurements.store import Measurement, Store

__all__ = ["SCHEMA_VERSION", "Measurement", "Store", "open_database"]
