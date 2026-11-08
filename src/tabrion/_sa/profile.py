"""Layer-B profile and TLS modes. Private: use ``tabrion.Profile`` and ``tabrion.TLSMode``.

Contract: ``docs/api/contract.md`` section 5.1; defaults in the specification section 4.3 (TAB-018).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import enum
import math
import re

from tabrion.errors import ConfigurationError
from tabrion.postgres import SessionSettings
from tabrion.sqlite import SQLiteProfile, Synchronous

__all__ = ["Profile", "TLSMode"]

_APPLICATION_NAME = re.compile(r"[\x20-\x7e]{0,63}")
"""PostgreSQL keeps 63 bytes of ``application_name``; ``tabrion.postgres.configure`` validates the same way."""


class TLSMode(enum.StrEnum):
    AUTO = "auto"
    VERIFY_FULL = "verify-full"
    REQUIRE = "require"
    INSECURE = "insecure"


def _invalid(field: str) -> ConfigurationError:
    # The field name only, never the value (contract I-37).
    return ConfigurationError("config.invalid_profile", details={"field": field})


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_seconds(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value > 0


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

    def __post_init__(self) -> None:
        # Validation in the style of SQLiteProfile: ConfigurationError("config.invalid_profile") naming the field.
        if not isinstance(self.sqlite, SQLiteProfile):
            raise _invalid("sqlite")
        if not isinstance(self.postgres, SessionSettings):
            raise _invalid("postgres")
        name = self.application_name
        if not isinstance(name, str) or _APPLICATION_NAME.fullmatch(name) is None:
            raise _invalid("application_name")
        if not _is_int(self.pool_size) or self.pool_size < 1:
            raise _invalid("pool_size")
        if not _is_int(self.max_overflow) or self.max_overflow < 0:
            raise _invalid("max_overflow")
        for field in ("pool_timeout", "pool_recycle", "connect_timeout"):
            if not _is_seconds(getattr(self, field)):
                raise _invalid(field)
        if not isinstance(self.pool_pre_ping, bool):
            raise _invalid("pool_pre_ping")
        try:
            tls = TLSMode(self.tls)
        except ValueError:
            tls = None
        if tls is None:
            raise _invalid("tls")
        object.__setattr__(self, "tls", tls)
        if not _is_int(self.max_rows) or self.max_rows < 1:
            raise _invalid("max_rows")
        if not isinstance(self.read_only, bool):
            raise _invalid("read_only")

    @classmethod
    def default(cls) -> Profile:
        return cls()

    @classmethod
    def readonly(cls) -> Profile:
        """``read_only=True``; the Studio default."""
        return cls(read_only=True)

    @classmethod
    def edge(cls) -> Profile:
        """SQLite ``synchronous=NORMAL`` and ``secure_delete=False`` for bulk measurements."""
        return cls(sqlite=replace(SQLiteProfile(), synchronous=Synchronous.NORMAL, secure_delete=False))

    @classmethod
    def testing(cls) -> Profile:
        """Statement timeout 5 s, lock timeout 2 s, pool size 2."""
        # SQLite has no statement timeout of its own; its lock wait is the busy timeout (contract I-37).
        return cls(
            sqlite=replace(SQLiteProfile(), busy_timeout=2.0),
            postgres=replace(SessionSettings(), statement_timeout_ms=5_000, lock_timeout_ms=2_000),
            pool_size=2,
        )
