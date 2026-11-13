"""Job "core without extras" (05 section 2.3, AC-3): only ``pip install . pytest`` is installed.

Not collected by the normal suite (no ``test_`` prefix); the job passes this file explicitly:
``pytest tests/packaging/core_without_extras.py --noconftest -p no:cacheprovider``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys

import pytest

CORE = ("tabrion", "tabrion.errors", "tabrion.sql", "tabrion.config", "tabrion.sqlite", "tabrion.check")


def test_no_optional_dependency_is_installed() -> None:
    for name in ("psycopg", "psycopg_pool", "sqlalchemy", "alembic"):
        assert importlib.util.find_spec(name) is None, f"{name} is installed"


def test_tabrion_requires_nothing() -> None:
    """TAB-T-180 for the installed distribution: no requirement outside an extra."""
    requires = importlib.metadata.requires("tabrion") or []
    assert requires, "the extras are missing from the metadata"
    assert all("extra ==" in r for r in requires), requires


@pytest.mark.parametrize("module_name", CORE)
def test_core_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


def test_core_loads_no_driver() -> None:
    for module_name in CORE:
        importlib.import_module(module_name)
    for name in ("psycopg", "sqlalchemy", "alembic"):
        assert name not in sys.modules


def test_layer_b_and_migrations_give_the_installation_hint() -> None:
    import tabrion

    with pytest.raises(
        ImportError, match=r'pip install "tabrion\[sqlalchemy\] @ git\+https://github\.com/peterkarlhuber/tabrion@'
    ):
        _ = tabrion.Database
    with pytest.raises(ImportError, match=r"tabrion\[migrations\]"):
        importlib.import_module("tabrion.migrations")
