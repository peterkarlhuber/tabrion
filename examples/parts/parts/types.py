"""Portable column types (specification section 6): UTC timestamps and exact decimals on both engines.

SQLite has no time type and turns ``NUMERIC`` into ``float``; these types store ISO 8601 text and decimal text there
and use ``timestamptz`` and ``numeric(p, s)`` on PostgreSQL, the mappings of TAB-T-D01 and D02 (contract I-58).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import DateTime, Dialect, Numeric, String
from sqlalchemy.types import TypeDecorator, TypeEngine

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class UtcDateTime(TypeDecorator[datetime]):
    """An aware ``datetime`` stored in UTC: ``timestamptz`` on PostgreSQL, ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` on SQLite.

    A naive ``datetime`` is refused; values come back aware, with ``tzinfo=UTC``.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("a timestamp must be a timezone-aware datetime")
        value = value.astimezone(UTC)
        return value.strftime(_TS_FORMAT) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=UTC)
        return value.astimezone(UTC)  # type: ignore[no-any-return]


class ExactDecimal(TypeDecorator[Decimal]):
    """A ``Decimal`` with ``precision`` digits, ``scale`` of them after the point: ``numeric(p, s)`` on PostgreSQL,
    decimal text on SQLite (never a ``float``). A ``float`` is refused, so no rounding error enters.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        super().__init__(precision=precision, scale=scale, asdecimal=True)
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(self.precision + 2))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def _exact(self, value: object) -> Decimal:
        if not isinstance(value, Decimal | int) or isinstance(value, bool):
            raise ValueError("a decimal value must be a Decimal or an int, never a float")
        try:
            exact = Decimal(value).quantize(Decimal(1).scaleb(-self.scale))
        except InvalidOperation:
            exact = None
        if exact is None or not exact.is_finite() or len(exact.as_tuple().digits) > self.precision:
            raise ValueError(f"a decimal value must fit numeric({self.precision}, {self.scale})")
        if exact != value:
            raise ValueError(f"a decimal value must have at most {self.scale} digits after the point")
        return exact

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        exact = self._exact(value)
        return str(exact) if dialect.name == "sqlite" else exact

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value) if isinstance(value, str) else self._exact(Decimal(value))
