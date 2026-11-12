"""Measurements reference application, plain SQLAlchemy baseline (TABRION RA-B-4), for the RA-EVAL comparison.

The same features as ``examples/measurements`` (capture on SQLite, idempotent transfer upstream) and the same
layout, file by file, without Tabrion: ``db.py`` and ``upstream.py`` are the database infrastructure (metric MW-1),
``store.py``, ``transfer.py`` and ``cli.py`` the domain code.
"""

from measurements_baseline.db import SCHEMA_VERSION, open_database
from measurements_baseline.store import Measurement, Store

__all__ = ["SCHEMA_VERSION", "Measurement", "Store", "open_database"]
