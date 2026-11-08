"""G-T1 condition C-4 (G-10): a consumer passes its ``RoleSetup`` by overriding the fixture ``tabrion_role_setup``."""

from __future__ import annotations

from collections.abc import Callable
import inspect
from typing import Any

import pytest

pytest_plugins = ["pytester"]


def _wrapped(fixture: object) -> inspect.Signature:
    fn: Callable[..., Any] = getattr(fixture, "__wrapped__", None) or fixture._get_wrapped_function()  # type: ignore[attr-defined]
    return inspect.signature(fn)


def test_database_fixture_requests_the_role_setup() -> None:
    from tabrion.testing import postgres

    assert "tabrion_role_setup" in _wrapped(postgres.tabrion_postgres_database).parameters


def test_role_setup_default_and_override(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        """
        import pytest

        from tabrion.testing import RoleSetup

        pytest_plugins = ["tabrion.testing"]


        @pytest.fixture(scope="session")
        def tabrion_role_setup():
            return RoleSetup(app="app_role", split=False)
        """
    )
    pytester.makepyfile(
        """
        from tabrion.testing import RoleSetup


        def test_override(tabrion_role_setup):
            assert tabrion_role_setup == RoleSetup(app="app_role", split=False)
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)


def test_role_setup_default(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["tabrion.testing"]\n')
    pytester.makepyfile(
        """
        from tabrion.testing import RoleSetup


        def test_default(tabrion_role_setup):
            assert tabrion_role_setup == RoleSetup()
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
