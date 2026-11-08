"""TAB-005 / QA-5 / AC-3 (TAB-T-182, TAB-T-E16): the core never loads an optional dependency.

The checks run in a fresh interpreter, because this test process has the extras installed and loaded.
"""

from __future__ import annotations

from tests.packaging.blocking import BLOCK_SOURCE
from tests.support import isolation_check_source, run_python


def test_core_imports_load_no_optional_dependency() -> None:
    """TAB-T-182/E16: ``import tabrion.sqlite`` (and the other core modules) loads neither psycopg nor SQLAlchemy."""
    result = run_python(isolation_check_source())
    assert result.returncode == 0, result.stderr
    assert "core isolated" in result.stdout


def test_core_imports_with_blocked_extras() -> None:
    """The core imports while psycopg, SQLAlchemy and Alembic are blocked (``sys.modules[name] = None``)."""
    result = run_python(BLOCK_SOURCE + isolation_check_source())
    assert result.returncode == 0, result.stderr


def test_postgres_module_imports_without_sqlalchemy() -> None:
    """``tabrion.postgres`` needs at most psycopg: it imports with SQLAlchemy and Alembic blocked."""
    code = (
        "import sys\n"
        "for n in ('sqlalchemy', 'alembic', 'psycopg_pool'):\n"
        "    sys.modules[n] = None\n"
        "import tabrion.postgres, tabrion.postgres.roles, tabrion.postgres.locks\n"
        "assert 'sqlalchemy' not in [m for m in sys.modules if sys.modules[m] is not None]\n"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr


def test_layer_b_names_give_the_installation_hint_without_sqlalchemy() -> None:
    code = BLOCK_SOURCE + (
        "import tabrion\n"
        "for name in ('Database', 'Profile', 'Transaction', 'TLSMode', 'Health', 'Result', 'Column'):\n"
        "    try:\n"
        "        getattr(tabrion, name)\n"
        "    except ImportError as exc:\n"
        "        assert 'pip install \"tabrion[sqlalchemy]\"' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError(name + ' imported without SQLAlchemy')\n"
        "assert tabrion.safe_url is not None and tabrion.TabrionError is not None\n"
        "try:\n"
        "    import tabrion.migrations\n"
        "except ImportError as exc:\n"
        "    assert 'tabrion[migrations]' in str(exc), exc\n"
        "else:\n"
        "    raise AssertionError('tabrion.migrations imported without Alembic')\n"
        "print('hints ok')\n"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    assert "hints ok" in result.stdout


def test_testing_gives_the_installation_hint_without_pytest() -> None:
    code = (
        "import sys\n"
        "sys.modules['pytest'] = None\n"
        "try:\n"
        "    import tabrion.testing\n"
        "except ImportError as exc:\n"
        "    assert 'tabrion[testing]' in str(exc), exc\n"
        "else:\n"
        "    raise AssertionError('tabrion.testing imported without pytest')\n"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
