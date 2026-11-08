"""SQLAlchemy ``connect``/``begin``/``reset`` listeners that apply layer A to the same DBAPI connection.

Private. Specification section 3.1; implemented in TAB-018. There is no second connection, no own pool and no
adapter between the layers.
"""

from __future__ import annotations

__all__: list[str] = []
