"""Layer B: a thin wrapper around a SQLAlchemy ``Engine`` (extra ``sqlalchemy``). Private: use ``tabrion.*``.

Importing this package without SQLAlchemy fails; ``tabrion.Database`` and the other layer-B names turn that into an
``ImportError`` with the installation hint (``tabrion.__getattr__``).
"""

from __future__ import annotations

import sqlalchemy as _sqlalchemy  # noqa: F401  # layer B needs the extra "sqlalchemy"
