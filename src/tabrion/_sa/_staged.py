"""The 0.2 classes before 0.2: ``Database`` and ``Transaction`` with the staged mixins. Private, for tests only.

G-T1 condition C-6 keeps the 0.2 methods off ``tabrion.Database`` and ``tabrion.Transaction`` while the package
version is 0.1. Tabrion's own tests reach them through these subclasses, which combine the mixins of
``tabrion._sa._v02`` with the 0.1 classes exactly as 0.2 will (TODO TB-013: ``class Transaction(_TransactionV02)``,
``class Database(_DatabaseV02)``, then this module goes). Not part of any API, not documented for consumers, free to
change or vanish in any release (contract implementation note I-106).
"""

from __future__ import annotations

from typing import ClassVar

from tabrion._sa._v02 import _DatabaseV02, _TransactionV02
from tabrion._sa.database import Database, Transaction

__all__: list[str] = []


class _StagedTransaction(_TransactionV02, Transaction):
    """``Transaction`` as of 0.2."""


class _StagedDatabase(_DatabaseV02, Database):
    """``Database`` as of 0.2: ``from_url``, ``from_env`` and ``from_engine`` create this class."""

    _transaction_class: ClassVar[type[Transaction]] = _StagedTransaction
