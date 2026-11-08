"""The settings of :func:`tabrion.testing.configure` (contract section 6, I-36). Standard library only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
import re

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PREFIX = re.compile(r"[a-z_][a-z0-9_]*")
PREFIX_MAX = 24
"""Longest ``schema_prefix``: the longest generated name, ``<prefix>rt_<hex12>``, stays well below 63 bytes."""


@dataclass(frozen=True, slots=True)
class Settings:
    postgres_env: tuple[str, ...]
    require_env: tuple[str, ...]
    runtime_role_env: tuple[str, ...]
    require_extensions_env: tuple[str, ...]
    schema_prefix: str

    def first(self, names: tuple[str, ...]) -> tuple[str, str] | None:
        """``(name, value)`` of the first of ``names`` that is set and not empty."""
        for name in names:
            value = os.environ.get(name, "")
            if value:
                return name, value
        return None

    def flag(self, names: tuple[str, ...]) -> bool:
        """Whether one of ``names`` is ``1``."""
        return any(os.environ.get(name, "") == "1" for name in names)

    def required_extensions(self) -> frozenset[str]:
        """The extension names of the require variables (comma or white space separated)."""
        found: set[str] = set()
        for name in self.require_extensions_env:
            found.update(part for part in re.split(r"[\s,]+", os.environ.get(name, "")) if part)
        return frozenset(found)


def _names(value: Sequence[str] | None, default: str, argument: str) -> tuple[str, ...]:
    if value is None:
        return (default,)
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise TypeError(f"{argument} must be a sequence of environment variable names (not a str)")
    names = tuple(value)
    if not names:
        raise ValueError(f"{argument} must name at least one environment variable")
    for name in names:
        if not isinstance(name, str):
            raise TypeError(f"{argument} must hold str names")
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"{argument}: an environment variable name matches [A-Za-z_][A-Za-z0-9_]*")
    return names


def build(
    *,
    env_prefix: str,
    postgres_env: Sequence[str] | None,
    require_env: Sequence[str] | None,
    runtime_role_env: Sequence[str] | None,
    require_extensions_env: Sequence[str] | None,
    schema_prefix: str,
) -> Settings:
    if not isinstance(env_prefix, str):
        raise TypeError("env_prefix must be a str")
    if not _ENV_NAME.fullmatch(env_prefix):
        raise ValueError("env_prefix must match [A-Za-z_][A-Za-z0-9_]*")
    if not isinstance(schema_prefix, str):
        raise TypeError("schema_prefix must be a str")
    if not _PREFIX.fullmatch(schema_prefix) or len(schema_prefix) > PREFIX_MAX:
        raise ValueError(f"schema_prefix must match [a-z_][a-z0-9_]* and have at most {PREFIX_MAX} characters")
    return Settings(
        postgres_env=_names(postgres_env, f"{env_prefix}_TEST_POSTGRES", "postgres_env"),
        require_env=_names(require_env, f"{env_prefix}_REQUIRE_POSTGRES", "require_env"),
        runtime_role_env=_names(runtime_role_env, f"{env_prefix}_TEST_RUNTIME_ROLE", "runtime_role_env"),
        require_extensions_env=_names(
            require_extensions_env, f"{env_prefix}_REQUIRE_EXTENSIONS", "require_extensions_env"
        ),
        schema_prefix=schema_prefix,
    )


DEFAULT = build(
    env_prefix="TABRION",
    postgres_env=None,
    require_env=None,
    runtime_role_env=None,
    require_extensions_env=None,
    schema_prefix="t_",
)
current: Settings = DEFAULT
"""The settings in force; replaced as a whole by ``configure``."""
