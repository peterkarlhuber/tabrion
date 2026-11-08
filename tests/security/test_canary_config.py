"""QA-2: canary passwords and tokens through ``tabrion.config``, layer B URLs and ``tabrion.testing`` (TAB-T-110..112).

No database server is needed: the PostgreSQL cases connect to a closed local port. The canary is the password (in
URL form, keyword conninfo and a ``password`` query parameter), a token in a ``*_FILE`` secret file, or a value in a
URL option; none may appear in an error, a ``repr``, ``safe_url``, a warning, a log record or pytest's own report.
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from pathlib import Path
import socket

import pytest

from tabrion.config import safe_url, url_from_env
from tabrion.errors import ConfigurationError, TabrionError
from tests.canary import assert_clean, assert_logs_clean, new_canary

pytestmark = pytest.mark.security


@pytest.fixture
def canary() -> str:
    return new_canary()


@pytest.fixture(autouse=True)
def tabrion_logs(caplog: pytest.LogCaptureFixture, canary: str) -> Iterator[None]:
    caplog.set_level(logging.DEBUG, logger="tabrion")
    yield
    assert_logs_clean(caplog.records, canary)


def closed_port() -> int:
    """A local TCP port nobody listens on (bound and released; nothing else on the runner takes it that fast)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def urls(canary: str, port: int = 5432) -> list[str]:
    return [
        f"postgresql://app:{canary}@127.0.0.1:{port}/db",
        f"postgresql+psycopg://app:{canary}@127.0.0.1:{port}/db?sslmode=disable",
        f"postgresql://app@127.0.0.1:{port}/db?password={canary}",
        f"host=127.0.0.1 port={port} user=app password={canary} dbname=db",
        f"host=127.0.0.1 port={port} user=app password='{canary} x' dbname=db",
    ]


# --- TAB-T-111: safe_url ---------------------------------------------------------------------------------------------


def test_t111_safe_url_masks_every_form(canary: str) -> None:
    for url in urls(canary):
        masked = safe_url(url)
        assert canary not in masked, masked
        assert masked != "<unparseable url>", url


def test_t111_safe_url_unparseable_never_echoes(canary: str) -> None:
    for broken in (f"postgresql://app:{canary}@[::1", f"password='{canary}", f"\x00{canary}"):
        assert canary not in safe_url(broken)


# --- TAB-T-112: url_from_env and *_FILE ------------------------------------------------------------------------------


def test_t112_url_from_env_errors_name_no_value(tmp_path: Path, canary: str) -> None:
    secret = tmp_path / f"{canary}.secret"
    secret.write_text(urls(canary)[0] + "\n")
    os.chmod(secret, 0o600)
    cases = [
        {"DB": urls(canary)[0], "DB_FILE": str(secret)},  # ambiguous
        {"DB_FILE": str(tmp_path / canary / "missing")},  # unreadable
        {"DB_FILE": str(tmp_path)},  # not a regular file
    ]
    big = tmp_path / "big"
    big.write_text(canary * 5000)
    cases.append({"DB_FILE": str(big)})  # too large
    undecodable = tmp_path / "latin1"
    undecodable.write_bytes(canary.encode() + b"\xff\xfe")
    cases.append({"DB_FILE": str(undecodable)})
    for environ in cases:
        with pytest.raises(ConfigurationError) as info:
            url_from_env("DB", environ=environ)
        assert_clean(info.value, canary)
    with pytest.raises(ConfigurationError) as missing:
        url_from_env("DB", environ={"OTHER": canary})
    assert_clean(missing.value, canary)
    assert url_from_env("DB", environ={"DB_FILE": str(secret)}) == urls(canary)[0]


# --- Layer B: URL, repr, configuration errors, connection failures ----------------------------------------------------

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")
from tabrion import Database, Profile, TLSMode  # noqa: E402
from tabrion.errors import ConnectionFailed  # noqa: E402


def test_t110_layer_b_repr_and_safe_url(canary: str) -> None:
    port = closed_port()
    for url in urls(canary, port)[:3]:
        database = Database.from_url(url, profile=Profile(tls=TLSMode.INSECURE))
        try:
            assert canary not in repr(database) and canary not in database.safe_url
        finally:
            database.close()


def test_t110_layer_b_configuration_errors(canary: str) -> None:
    bad = [
        (f"postgresql+psycopg2://app:{canary}@db.example/x", {}),  # driver
        (f"postgresql://app:{canary}@db.example/x?sslmode=disable", {}),  # TLS rule
        (f"mysql://app:{canary}@db.example/x", {}),
        (f"sqlite:///x.db?password={canary}", {}),  # SQLite URL options
        (f"postgresql://app:{canary}@db.example/x", {"connect_args": {"password": canary}, "bogus": canary}),
    ]
    for url, options in bad:
        with pytest.raises((TabrionError, TypeError, ValueError, sqlalchemy.exc.ArgumentError)) as info:
            Database.from_url(url, engine_options=options).close()
        if isinstance(info.value, TabrionError):
            assert_clean(info.value, canary)
        else:  # refused by SQLAlchemy itself before Tabrion saw the URL
            assert canary not in str(info.value) and canary not in repr(info.value)


def test_t110_layer_b_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, canary: str) -> None:
    secret = tmp_path / "db.secret"
    secret.write_text(f"postgresql+psycopg2://app:{canary}@db.example/x\n")
    os.chmod(secret, 0o600)
    monkeypatch.setenv("CANARY_DB_FILE", str(secret))
    with pytest.raises(ConfigurationError) as driver:
        Database.from_env("CANARY_DB")
    assert_clean(driver.value, canary)
    monkeypatch.setenv("CANARY_DB", f"postgresql://app:{canary}@db.example/x")
    with pytest.raises(ConfigurationError) as ambiguous:
        Database.from_env("CANARY_DB")
    assert_clean(ambiguous.value, canary)


@pytest.mark.parametrize("form", range(5))
def test_t110_layer_b_connection_failure(canary: str, form: int) -> None:
    """``ConnectionFailed`` for a refused connection: no password in the error or SQLAlchemy's wrapped original."""
    url = urls(canary, closed_port())[form]
    if not url.startswith("postgresql"):
        url = "postgresql+psycopg:///?" + "&".join(url.replace("password='", "password=").replace("'", "").split(" "))
        url = url.replace(" x", "")
    database = Database.from_url(url, profile=Profile(tls=TLSMode.INSECURE, connect_timeout=2, pool_pre_ping=False))
    try:
        with pytest.raises(ConnectionFailed) as info:
            database.fetch_scalar("SELECT 1")
    finally:
        database.close()
    assert_clean(info.value, canary)
    assert canary not in str(info.value.original)  # libpq names host and port, never the password


def test_t110_layer_b_check_without_server(canary: str) -> None:
    """``Database.check`` and ``health`` when the server is unreachable: findings and errors carry no password."""
    database = Database.from_url(
        f"postgresql://app:{canary}@127.0.0.1:{closed_port()}/db",
        profile=Profile(tls=TLSMode.INSECURE, connect_timeout=2, pool_pre_ping=False),
    )
    try:
        for call in (database.check, database.health):
            try:
                result = call()
            except TabrionError as exc:
                assert_clean(exc, canary)
            else:
                assert canary not in repr(result)
    finally:
        database.close()


# --- tabrion.testing: pytest's own report never shows the password ----------------------------------------------------

INNER = """
import pytest

def test_uses_url(tabrion_postgres_url):
    assert not tabrion_postgres_url  # fails: pytest shows the fixture value with repr (masked)

def test_uses_schema(tabrion_postgres_schema):
    assert False, "never reached"

def test_uses_database(tabrion_postgres_database):
    assert False, "never reached"

def test_uses_roles(tabrion_postgres_roles):
    assert False, "never reached"

def test_server_version(postgres_server_version):
    assert False, "never reached"

def test_truncate(tabrion_postgres_url):
    from tabrion.testing import truncate_tables
    truncate_tables(tabrion_postgres_url)  # the fixture value: its repr is masked
"""


def test_t110_testing_report_masks_the_url(pytester: pytest.Pytester, canary: str) -> None:
    """A failing test with ``-l`` (locals and fixture values) and fixtures that cannot reach the server: the
    password is in no line of pytest's output (contract I-36, I-75). Before I-75, psycopg's frames in the default
    report printed it as the ``conninfo`` argument."""
    pytester.makepyfile(INNER)
    port = closed_port()
    url = f"host=127.0.0.1 port={port} user=app password={canary} dbname=db connect_timeout=2"
    pytester._monkeypatch.setenv("TABRION_TEST_POSTGRES", url)
    pytester._monkeypatch.setenv("TABRION_REQUIRE_POSTGRES", "1")
    output = ""
    for options in ((), ("-l", "-vv", "--tb=long", "-rA"), ("--tb=native",), ("--full-trace",)):
        result = pytester.runpytest_subprocess("-p", "tabrion.testing", *options)
        output += result.stdout.str() + result.stderr.str()
    result.assert_outcomes(failed=2, errors=4)
    assert "password=***" in output and "cannot connect to" in output
    assert canary not in output, output
