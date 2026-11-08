"""TAB-012: ``tabrion.config.safe_url`` and ``url_from_env`` (contract section 2.3, TAB-T-C01..C08).

Acceptance (plan document 09): the password never appears in ``safe_url``, in ``repr`` or in an error text.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from tabrion import safe_url as root_safe_url
from tabrion.config import safe_url, url_from_env
from tabrion.errors import ConfigurationError

SECRET = "SECRET-4711"

# --- TAB-T-C01: URL form -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:SECRET-4711@h/db", "postgresql://u:***@h/db"),
        ("postgresql+psycopg://u:SECRET-4711@h:5432/db", "postgresql+psycopg://u:***@h:5432/db"),
        (
            "postgres://u:SECRET-4711@h1:5432,h2:5433/db?sslmode=require",
            "postgres://u:***@h1:5432,h2:5433/db?sslmode=require",
        ),
        ("postgresql://u:SECRET-4711@[::1]:5432/db", "postgresql://u:***@[::1]:5432/db"),
        # Raw "@", "/", "#" and ":" in a password (not percent-encoded): everything up to the last "@" is hidden.
        ("postgresql://u:SEC@RET-4711@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:SEC/RET#4711@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:a:SECRET-4711@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:SECRET%404711@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:@h/db", "postgresql://u:***@h/db"),
        # Review M-1: libpq ends the user information at the last "@" before the first "/".
        ("postgresql://u:a?b=c@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:a?password=x@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:a&b@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:SEC?RET-4711@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:SEC?RET-4711@h", "postgresql://u:***@h"),
        # Nothing to hide.
        ("postgresql://u@h/db", "postgresql://u@h/db"),
        ("postgresql://h/db", "postgresql://h/db"),
        ("postgresql:///db?host=/run/postgresql", "postgresql:///db?host=/run/postgresql"),
        ("sqlite:///var/lib/app.db", "sqlite:///var/lib/app.db"),
        ("postgresql://h:5432/db?application_name=a@b", "postgresql://h:5432/db?application_name=a@b"),
        # Already masked (SQLAlchemy's repr): unchanged.
        ("postgresql+psycopg://u:***@h/db", "postgresql+psycopg://u:***@h/db"),
    ],
)
def test_c01_url_password_is_masked(url: str, expected: str) -> None:
    assert safe_url(url) == expected
    assert "4711" not in safe_url(url)


# --- TAB-T-C02: keyword conninfo -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("conninfo", "expected"),
    [
        ("host=h user=u password=SECRET-4711 dbname=d", "host=h user=u password=*** dbname=d"),
        ("password=SECRET-4711", "password=***"),
        ("host=h password = SECRET-4711  dbname=d", "host=h password = ***  dbname=d"),
        ("host=h password='SECRET 4711' dbname=d", "host=h password=*** dbname=d"),
        ("host=h password='SECRET\\' 4711' dbname=d", "host=h password=*** dbname=d"),
        ("host=h password=SECRET\\ 4711 dbname=d", "host=h password=*** dbname=d"),
        ("host=h sslpassword=SECRET-4711", "host=h sslpassword=***"),
        ("host=h PASSWORD=SECRET-4711", "host=h PASSWORD=***"),
        ("host=h password=", "host=h password=***"),
        ("host=h user=u dbname=d", "host=h user=u dbname=d"),
        ("", ""),
    ],
)
def test_c02_conninfo_password_is_masked(conninfo: str, expected: str) -> None:
    assert safe_url(conninfo) == expected
    assert "4711" not in safe_url(conninfo)


# --- TAB-T-C03: query parameter "password" ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u@h/db?password=SECRET-4711", "postgresql://u@h/db?password=***"),
        (
            "postgresql://h/db?sslmode=require&password=SECRET-4711&x=1",
            "postgresql://h/db?sslmode=require&password=***&x=1",
        ),
        ("postgresql://h/db?Password=SECRET-4711#frag", "postgresql://h/db?Password=***#frag"),
        ("postgresql://u:SECRET-4711@h/db?sslpassword=SECRET-4711", "postgresql://u:***@h/db?sslpassword=***"),
        ("postgresql://h/db?my_password_hint=x", "postgresql://h/db?my_password_hint=x"),
    ],
)
def test_c03_password_query_parameter_is_masked(url: str, expected: str) -> None:
    assert safe_url(url) == expected


# --- TAB-T-C04: unparseable input, never raises ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "host=h password='SECRET-4711",  # unterminated quote
        "host=h =SECRET-4711",
        "u:SECRET-4711@h/db",  # no scheme, no keyword list
        "postgresql://u:SEC/R?ET-4711@h/db",  # raw "/" and "?" in the password: refused rather than guessed
    ],
)
def test_c04_unparseable_input(value: str) -> None:
    assert safe_url(value) == "<unparseable url>"


def test_c04_non_string_input() -> None:
    assert safe_url(None) == "<unparseable url>"  # type: ignore[arg-type]
    assert safe_url(b"postgresql://u:p@h/db") == "<unparseable url>"  # type: ignore[arg-type]


_PIECES = st.sampled_from(
    ["postgresql://", "u", ":", "@", "/", "?", "&", "#", "=", "'", "\\", " ", "password", "host=h ", "x", "%40"]
)


@settings(max_examples=1000)
@given(st.lists(_PIECES, max_size=14).map("".join), st.text(max_size=30))
def test_c04_never_raises(prefix: str, noise: str) -> None:
    assert isinstance(safe_url(prefix + noise), str)


@settings(max_examples=1000)
@given(
    st.sampled_from(["postgresql://", "postgresql+psycopg://", "postgres://"]),
    st.text(alphabet=st.characters(blacklist_characters="@?", blacklist_categories=["Cs"]), max_size=12),
    st.text(alphabet=st.characters(blacklist_characters="@?/", blacklist_categories=["Cs"]), min_size=1, max_size=12),
)
def test_c04_password_never_survives(scheme: str, password_core: str, host: str) -> None:
    """Whatever the password holds (raw "/", "#", ":"), it does not come back."""
    password = f"SECRET{password_core}4711"
    masked = safe_url(f"{scheme}user:{password}@{host}/db")
    assert password not in masked


def test_c05_root_reexport() -> None:
    assert root_safe_url is safe_url


# --- TAB-T-C05..C08: url_from_env ------------------------------------------------------------------------------------

NAME = "TABRION_TEST_URL"


def test_c05_reads_the_variable() -> None:
    assert url_from_env(NAME, environ={NAME: "postgresql://h/db"}) == "postgresql://h/db"


def test_c05_reads_the_file_without_the_trailing_newline(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    for content, expected in [
        ("postgresql://u:p@h/db\n", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db\r\n", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db\n\n", "postgresql://u:p@h/db\n"),
        (" postgresql://h/db \n", " postgresql://h/db "),
    ]:
        secret.write_bytes(content.encode("utf-8"))
        assert url_from_env(NAME, environ={NAME + "_FILE": str(secret)}) == expected


def test_c05_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NAME, "postgresql://h/db")
    monkeypatch.delenv(NAME + "_FILE", raising=False)
    assert url_from_env(NAME) == "postgresql://h/db"


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_c05_symlink_to_a_regular_file_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("postgresql://h/db\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert url_from_env(NAME, environ={NAME + "_FILE": str(link)}) == "postgresql://h/db"


def test_c06_both_set_is_ambiguous(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    secret.write_text(f"postgresql://u:{SECRET}@h/db", encoding="utf-8")
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME: f"postgresql://u:{SECRET}@h/db", NAME + "_FILE": str(secret)})
    assert info.value.code == "config.ambiguous"
    _assert_no_value(info.value, SECRET, str(secret))


def test_c07_missing() -> None:
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={})
    assert info.value.code == "config.missing"
    assert info.value.details == {"name": NAME}
    assert url_from_env(NAME, environ={}, required=False) is None
    # An empty variable or an empty file counts as not set.
    assert url_from_env(NAME, environ={NAME: ""}, required=False) is None
    with pytest.raises(ConfigurationError, match=r"config\.missing"):
        url_from_env(NAME, environ={NAME: "", NAME + "_FILE": ""})


def test_c07_empty_file_counts_as_missing(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    secret.write_text("\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(secret)})
    assert info.value.code == "config.missing"
    assert url_from_env(NAME, environ={NAME + "_FILE": str(secret)}, required=False) is None


def test_c08_missing_file_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / f"no-{SECRET}"
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(path)}, required=False)
    assert info.value.code == "config.unreadable"
    assert info.value.details == {"name": NAME + "_FILE"}
    _assert_no_value(info.value, SECRET, str(path))


def test_c08_directory_is_not_a_regular_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(tmp_path)})
    # Windows cannot open a directory at all; everywhere else it is opened and refused by fstat (review L-1).
    expected = "config.unreadable" if sys.platform == "win32" else "config.not_regular_file"
    assert info.value.code == expected


def test_c08_device_is_not_a_regular_file() -> None:
    """``os.devnull`` (``/dev/null``, Windows ``nul``) opens everywhere and is refused by ``fstat``."""
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": os.devnull})
    assert info.value.code == "config.not_regular_file"


def _open_fds() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(directory):
            return len(os.listdir(directory))
    return None


@pytest.mark.skipif(_open_fds() is None, reason="no file descriptor listing on this platform")
def test_c08_no_file_descriptor_leaks(tmp_path: Path) -> None:
    regular = tmp_path / "url"
    regular.write_text("postgresql://h/db", encoding="utf-8")
    before = _open_fds()
    for target in (tmp_path, regular, tmp_path / "missing"):
        for _ in range(20):
            with contextlib.suppress(ConfigurationError):
                url_from_env(NAME, environ={NAME + "_FILE": str(target)})
    assert _open_fds() == before


def test_c08_large_file_is_refused(tmp_path: Path) -> None:
    big = tmp_path / "big"
    big.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(big)})
    assert info.value.code == "config.too_large"
    exact = tmp_path / "exact"
    exact.write_bytes(b"x" * (64 * 1024))
    assert url_from_env(NAME, environ={NAME + "_FILE": str(exact)}) == "x" * (64 * 1024)


def test_c08_errors_carry_no_context(tmp_path: Path) -> None:
    for target in (tmp_path / "missing", tmp_path):
        with pytest.raises(ConfigurationError) as info:
            url_from_env(NAME, environ={NAME + "_FILE": str(target)})
        assert info.value.__context__ is None and info.value.__cause__ is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_c08_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(fifo)})
    assert info.value.code == "config.not_regular_file"


def test_c08_invalid_utf8_is_unreadable(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    secret.write_bytes(b"postgresql://u:\xff" + SECRET.encode() + b"@h/db")
    with pytest.raises(ConfigurationError) as info:
        url_from_env(NAME, environ={NAME + "_FILE": str(secret)})
    assert info.value.code == "config.unreadable"
    _assert_no_value(info.value, SECRET, str(secret))
    # L-3: no __context__ that still holds the file's bytes (UnicodeDecodeError.object) or the path.
    assert info.value.__cause__ is None and info.value.__context__ is None


def _assert_no_value(error: ConfigurationError, *values: str) -> None:
    for text in (str(error), repr(error), str(error.args), str(dict(error.details))):
        for value in values:
            assert value not in text
