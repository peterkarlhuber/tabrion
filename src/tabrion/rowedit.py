"""Row statements for the guided write transaction (since 0.3, experimental, extra ``sqlalchemy``; TAB-XW-6).

``insert_row``, ``update_row`` and ``delete_row`` build one-row SQLAlchemy Core statements with an optimistic
predicate on the old values; ``Transaction.execute_checked(statement, expect_rowcount=1)`` runs them and turns a
conflict (0 rows) into ``RowCountMismatch``. Contract section 5.11; ADR 0015 section 9 N-5. No upsert and no merge
(specification section 1.3).

Planned for 0.3 (ADR 0015 Consequences): while the package version is below 0.3 this module cannot be imported
(``tabrion._since``); the implementation is staged in ``tabrion._sa._rowedit``.
"""

from __future__ import annotations

from tabrion._since import released

if not released("0.3"):
    raise ImportError(
        "tabrion.rowedit is planned for Tabrion 0.3 and not available in this version", name="tabrion.rowedit"
    )

try:
    import sqlalchemy  # noqa: F401 - the extra's check, before the implementation imports it
except ImportError:
    raise ImportError(
        'tabrion.rowedit needs the extra "sqlalchemy": '
        'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
        " (Tabrion is not on PyPI: pin the commit you use)"
    ) from None

from tabrion._sa._rowedit import delete_row, insert_row, update_row

__all__ = ["delete_row", "insert_row", "update_row"]
