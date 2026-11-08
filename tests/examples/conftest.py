"""The reference applications under ``examples/`` run inside the normal suite (TODO TB-010: a dedicated job later)."""

from __future__ import annotations

from pathlib import Path
import sys

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
for application in ("measurements",):
    path = str(EXAMPLES / application)
    if path not in sys.path:
        sys.path.insert(0, path)
