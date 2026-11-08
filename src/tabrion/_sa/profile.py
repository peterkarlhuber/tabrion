"""Layer-B profile and TLS modes. Private: use ``tabrion.Profile`` and ``tabrion.TLSMode``.

Contract: ``docs/api/contract.md`` section 5.1; defaults in the specification section 4.3. Implemented in TAB-018.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum

from tabrion.postgres import SessionSettings
from tabrion.sqlite import SQLiteProfile

__all__ = ["Profile", "TLSMode"]


class TLSMode(enum.StrEnum):
    AUTO = "auto"
    VERIFY_FULL = "verify-full"
    REQUIRE = "require"
    INSECURE = "insecure"


@dataclass(frozen=True, slots=True)
class Profile:
    sqlite: SQLiteProfile = SQLiteProfile()
    postgres: SessionSettings = SessionSettings()
    application_name: str = "tabrion"
    pool_size: int = 5
    max_overflow: int = 0
    pool_timeout: float = 10.0
    pool_pre_ping: bool = True
    pool_recycle: float = 1800.0
    connect_timeout: float = 10.0
    tls: TLSMode = TLSMode.AUTO
    max_rows: int = 10_000
    read_only: bool = False
    """SQLite: ``mode=ro`` and ``query_only``; PostgreSQL: ``default_transaction_read_only=on``."""

    @classmethod
    def default(cls) -> Profile:
        raise NotImplementedError("tabrion.Profile.default: implemented in TAB-018")

    @classmethod
    def readonly(cls) -> Profile:
        """``read_only=True``; the Studio default."""
        raise NotImplementedError("tabrion.Profile.readonly: implemented in TAB-018")

    @classmethod
    def edge(cls) -> Profile:
        """SQLite ``synchronous=NORMAL`` and ``secure_delete=False`` for bulk measurements."""
        raise NotImplementedError("tabrion.Profile.edge: implemented in TAB-018")

    @classmethod
    def testing(cls) -> Profile:
        """Statement timeout 5 s, lock timeout 2 s, pool size 2."""
        raise NotImplementedError("tabrion.Profile.testing: implemented in TAB-018")
