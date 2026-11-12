"""Measurements reference application: local capture on SQLite (RA-B-1), idempotent transfer upstream (RA-B-2).

The package and its capture use layer A only (``tabrion.sqlite``, standard library). The transfer lives in
``measurements.transfer`` and ``measurements.upstream`` (layer B, extras ``sqlalchemy`` and ``postgres``), which
this module does not import: batches by the high-water mark ``seq``, ``ON CONFLICT DO NOTHING`` on the natural key
``(device_id, channel, ts_utc)``, the mark moved only after the upstream commit.
"""

from measurements.db import SCHEMA_VERSION, open_database
from measurements.store import Measurement, Store

__all__ = ["SCHEMA_VERSION", "Measurement", "Store", "open_database"]
