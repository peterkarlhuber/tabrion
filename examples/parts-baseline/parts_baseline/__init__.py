"""Parts reference application, plain SQLAlchemy baseline (TABRION RA-A-3).

The same schema, migrations and catalog functions as ``examples/parts``, with SQLAlchemy and Alembic alone: no
Tabrion import. The same test suite runs against both (``tests/examples/parts_suite.py``); where the baseline
behaves differently, the suite records it as an expected failure, a measurement for RA-EVAL.
"""

from parts_baseline.db import SchemaNotCurrent, create_engine, downgrade, require_current, status, upgrade
from parts_baseline.schema import STATUSES, metadata

__all__ = [
    "STATUSES",
    "SchemaNotCurrent",
    "create_engine",
    "downgrade",
    "metadata",
    "require_current",
    "status",
    "upgrade",
]
