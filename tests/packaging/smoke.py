"""Import smoke test for one extras combination, run inside a fresh virtual environment (05 section 2.2, K14).

Usage: ``python -I smoke.py <extras>`` with a comma-separated list (empty for the core). Standard library only; it
must not import anything from the repository's tests.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys

EXTRA_PACKAGES = {
    "postgres": {"psycopg"},
    "sqlalchemy": {"sqlalchemy"},
    "migrations": {"sqlalchemy", "alembic"},
    "testing": {"pytest"},
}
EXPANDS = {"all": {"postgres", "sqlalchemy", "migrations", "testing"}, "migrations": {"migrations", "sqlalchemy"}}
LEVUVO = ("orkexa", "axentra", "nodestro", "levuvo", "tabrion-studio", "tabrion_studio")


def _expand(extras: set[str]) -> set[str]:
    result = set()
    for extra in extras:
        result |= EXPANDS.get(extra, {extra})
    return result


def _fails(module: str, hint: str) -> None:
    try:
        importlib.import_module(module)
    except ImportError as exc:
        assert hint in str(exc), f"{module}: {exc}"
    else:
        raise AssertionError(f"{module} imported without its extra")


def main(arg: str) -> None:
    extras = _expand({e for e in arg.split(",") if e})
    expected = set().union(*(EXTRA_PACKAGES[e] for e in extras)) if extras else set()

    dists = {d.metadata["Name"].lower().replace("_", "-") for d in importlib.metadata.distributions()}
    assert "tabrion" in dists
    assert not [d for d in dists if d.startswith(LEVUVO)], dists
    if not extras:
        # TAB-T-180 / quality goal "dependency-light": the core installs no third-party package.
        assert dists <= {"tabrion", "pip", "setuptools", "wheel"}, f"the core pulled in {sorted(dists)}"

    for name in ("tabrion", "tabrion.errors", "tabrion.sql", "tabrion.config", "tabrion.sqlite", "tabrion.check"):
        importlib.import_module(name)
    # TAB-T-182: the core loads no optional dependency, even when it is installed.
    loaded = [n for n in ("psycopg", "sqlalchemy", "alembic", "pytest") if n in sys.modules]
    assert not loaded, f"core imports loaded {loaded}"

    import tabrion

    importlib.import_module("tabrion.postgres")
    if "postgres" in extras:
        importlib.import_module("psycopg")
    if "sqlalchemy" in extras:
        assert tabrion.Database.__name__ == "Database"
        assert tabrion.Profile().pool_size == 5
    else:
        try:
            _ = tabrion.Database
        except ImportError as exc:
            assert 'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@' in str(exc), exc
        else:
            raise AssertionError("tabrion.Database imported without SQLAlchemy")
    if "migrations" in extras:
        importlib.import_module("tabrion.migrations")
    else:
        _fails("tabrion.migrations", "tabrion[migrations]")
    if "testing" in extras:
        importlib.import_module("tabrion.testing")
    else:
        _fails("tabrion.testing", "tabrion[testing]")
    for package in expected:
        importlib.import_module(package)
    print(f"extras [{arg}] ok: {sorted(dists)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
