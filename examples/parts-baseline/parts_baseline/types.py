"""Portable column types (specification section 6): UTC timestamps and exact decimals on both engines.

A copy of ``examples/parts/parts/types.py``: Tabrion has no column types, so both variants of RA-A-3 need this code.

SQLite has no time type and turns ``NUMERIC`` into ``float``; these types store fixed-width ISO 8601 text and a scaled
integer ("cents") there and use ``timestamptz`` and ``numeric(p, s)`` on PostgreSQL, the mappings of TAB-T-D01 and
D02 (contract I-58). Both representations order and compare like the values they stand for, so ``ORDER BY``, ``<``,
``min``, ``max`` and uniqueness give the same rows on both engines; ``sum`` of one column is exact too, while the
total fits ``numeric(p, s)`` (a larger one cannot be read back on either engine). ``+`` and ``-`` with a bound
``Decimal`` and ``coalesce`` are portable as well. Any other SQL arithmetic is silently wrong on SQLite, by a factor
of ``10**s`` or truncated, without an error: ``avg``, multiplying or dividing (also by a constant), ``round``, ``abs``
and ``CAST``. Do it in Python on ``Decimal``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import BigInteger, DateTime, Dialect, Numeric, String
from sqlalchemy.types import TypeDecorator, TypeEngine

_INT64_DIGITS = 18
"""Every integer of up to 18 digits fits SQLite's signed 64-bit ``INTEGER``."""


class UtcDateTime(TypeDecorator[datetime]):
    """An aware ``datetime`` stored in UTC: ``timestamptz`` on PostgreSQL, ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` on SQLite.

    A naive ``datetime`` is refused; values come back aware, with ``tzinfo=UTC``. The SQLite text has a fixed width
    (the year has four digits, also below 1000), so it orders and compares chronologically against bound parameters;
    it does not compare with SQLite's own ``CURRENT_TIMESTAMP`` (``YYYY-MM-DD HH:MM:SS``).
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
        if dialect.name != "sqlite":
            return value
        # isoformat pads the year to four digits (strftime("%Y") does not below 1000).
        return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            return datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=UTC)
        return value.astimezone(UTC)  # type: ignore[no-any-return]


class ExactDecimal(TypeDecorator[Decimal]):
    """A ``Decimal`` with ``precision`` digits, ``scale`` of them after the point: ``numeric(p, s)`` on PostgreSQL,
    the scaled integer ``value * 10**scale`` on SQLite (specification D02, "cents"; never a ``float``). A ``float`` is
    refused, so no rounding error enters. ``precision`` is at most 18, so the integer fits SQLite's 64 bits.

    A comparison with a bound parameter (``column > Decimal(5)``) scales the parameter the same way. Raw SQL
    literals do not: write ``column > :limit`` with a bound ``Decimal``, never ``column > 5``.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        if not 0 <= scale <= precision <= _INT64_DIGITS:
            raise ValueError(f"ExactDecimal needs 0 <= scale <= precision <= {_INT64_DIGITS}")
        super().__init__(precision=precision, scale=scale, asdecimal=True)
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
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
        return int(exact.scaleb(self.scale)) if dialect.name == "sqlite" else exact

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return self._exact(Decimal(int(value)).scaleb(-self.scale))
        return self._exact(Decimal(value))
