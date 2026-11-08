"""The application's own errors: fixed texts that name what failed, never a value the caller passed."""

from __future__ import annotations


class PartsError(Exception):
    """Base class of the parts application's errors."""


class NotFound(PartsError):
    """No part (or material) with that number (or code)."""


class AlreadyExists(PartsError):
    """A part number or material code that is taken already."""


class StaleVersion(PartsError):
    """The optimistic lock: the part changed since the caller read its version. Read it again and retry."""


class InvalidTransition(PartsError):
    """A status change the life cycle does not allow."""


class InUse(PartsError):
    """The part or material is still referenced (a component of an assembly, the material of a part)."""


class CycleRefused(PartsError):
    """The relation would make a part a component of itself, directly or through other assemblies."""
