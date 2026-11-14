"""The 0.3 classes before 0.3: ``Database`` and ``Transaction`` with the staged mixin. Private, for tests only.

ADR 0015 (Consequences) keeps ``Transaction.execute_checked`` off ``tabrion.Transaction`` while the package version is
below 0.3 (``tabrion._since``), as G-T1 condition C-6 kept the 0.2 methods off the 0.1 classes. Tabrion's own tests
reach it through these subclasses, which add the mixin of ``tabrion._sa._v03`` to the public classes; from 0.3 on the
public ``Transaction`` has it already and the subclass adds nothing. Not part of any API, not documented for
consumers, free to change or vanish in any release (contract implementation note I-120).
"""

from __future__ import annotations

from typing import ClassVar

from tabrion._sa._v03 import _TransactionV03
from tabrion._sa.database import Database, Transaction

__all__: list[str] = []


class _StagedTransaction(Transaction, _TransactionV03):
    """``Transaction`` as of 0.3 (the mixin after the public class, so the order holds in both states)."""


class _StagedDatabase(Database):
    """``Database`` whose ``transaction()`` and ``read()`` yield :class:`_StagedTransaction`."""

    _transaction_class: ClassVar[type[Transaction]] = _StagedTransaction
