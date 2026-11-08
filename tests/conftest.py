"""Shared test configuration. Kept free of third-party imports: the job "core without extras" uses it too."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
