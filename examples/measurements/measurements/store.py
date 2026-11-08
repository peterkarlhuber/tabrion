"""Capture: validated measurements, written in batches of one write transaction each."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import sqlite3

from tabrion import sqlite as tsqlite

_INSERT = (
    "INSERT INTO measurement (device_id, channel, ts_utc, value, unit, quality) VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (device_id, channel, ts_utc) DO NOTHING"
)

Row = tuple[str, str, str, float, str, int]


def format_ts(moment: datetime) -> str:
    """Fixed-width UTC text, ``2026-11-08T12:00:00.000000Z``; a naive ``datetime`` is refused."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("ts_utc must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _name(value: object, field: str, longest: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= longest:
        raise ValueError(f"{field} must be a str of 1 to {longest} characters")
    return value


@dataclass(frozen=True, slots=True)
class Measurement:
    device_id: str
    channel: str
    ts_utc: datetime
    value: float
    unit: str
    quality: int = 0

    def row(self) -> Row:
        """The validated row; the messages name the field, never the value (metric MW-4)."""
        value = self.value
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError("value must be a finite number")
        quality = self.quality
        if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 255:
            raise ValueError("quality must be an int from 0 to 255")
        return (
            _name(self.device_id, "device_id", 64),
            _name(self.channel, "channel", 64),
            format_ts(self.ts_utc),
            float(value),
            _name(self.unit, "unit", 16),
            quality,
        )


@dataclass(frozen=True, slots=True)
class Stored:
    seq: int
    measurement: Measurement


class Store:
    """The capture API over an open, migrated connection (``measurements.db.open_database``)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def capture(self, batch: Iterable[Measurement]) -> int:
        """Store ``batch`` in one ``BEGIN IMMEDIATE`` transaction: all of it or nothing.

        Every measurement is validated before the transaction starts (``ValueError`` names its position, never its
        value). A reading whose natural key is already stored is skipped; the result counts the new rows.
        """
        rows: list[Row] = []
        invalid: str | None = None
        for position, measurement in enumerate(batch):
            try:
                rows.append(measurement.row())
            except ValueError as exc:
                invalid = f"measurement {position}: {exc}"
            if invalid is not None:
                raise ValueError(invalid)  # outside the except block: no context
        if not rows:
            return 0
        with tsqlite.write_transaction(self.connection) as connection:
            before = connection.total_changes
            connection.executemany(_INSERT, rows)
            return connection.total_changes - before

    def count(self) -> int:
        with tsqlite.read_transaction(self.connection) as connection:
            return int(connection.execute("SELECT count(*) FROM measurement").fetchone()[0])

    def after(self, seq: int = 0, *, limit: int = 1000) -> list[Stored]:
        """Rows with ``seq`` above ``seq``, in capture order: the batches of the transfer (RA-B-2)."""
        with tsqlite.read_transaction(self.connection) as connection:
            cursor = connection.execute(
                "SELECT seq, device_id, channel, ts_utc, value, unit, quality FROM measurement "
                "WHERE seq > ? ORDER BY seq LIMIT ?",
                (seq, limit),
            )
            return [
                Stored(row[0], Measurement(row[1], row[2], parse_ts(row[3]), row[4], row[5], row[6]))
                for row in cursor.fetchall()
            ]


def synthetic(
    count: int, *, device_id: str = "dev-1", channel: str = "temp", start: datetime | None = None
) -> Iterator[Measurement]:
    """``count`` readings one second apart, for the demo, the tests and timing runs."""
    first = (start or datetime(2026, 1, 1, tzinfo=UTC)).timestamp()
    for index in range(count):
        moment = datetime.fromtimestamp(first + index, tz=UTC)
        yield Measurement(device_id, channel, moment, 20.0 + (index % 100) / 10, "degC", 0)
