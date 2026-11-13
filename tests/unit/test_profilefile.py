"""Connection profile file format v1 (TABRION TAB-CR-1, contract section 5.9, I-109)."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback

import pytest

from tabrion import profilefile as pf
from tabrion.errors import ConfigurationError

SECRET = "SECRET-4711-hunter2"

STUDIO_EXAMPLE = """\
version = 1
[[profile]]
name = "parts-dev"
engine = "sqlite"            # sqlite | postgresql
path = "data/parts.db"       # relative to the project folder
environment = "dev"          # dev | test | prod

[[profile]]
name = "measurements-central"
engine = "postgresql"
host = "db.example.internal"
port = 5432
database = "measurements"
user = "studio_ro"
password = "keyring:"        # keyring:, env:NAME, file:PATH; never a plain password
sslmode = "verify-full"
environment = "prod"
read_only_role = true
statement_timeout_ms = 15000
"""


def _issues(text: str | bytes) -> list[tuple[str, str]]:
    with pytest.raises(pf.ProfileFileError) as info:
        pf.parse(text)
    assert info.value.code == "profilefile.invalid"
    assert isinstance(info.value, ConfigurationError)
    return [(issue.pointer, issue.code) for issue in info.value.issues if issue.severity == "error"]


def _one(body: str) -> str:
    return f'version = 1\n[[profile]]\nname = "x"\n{body}\n'


def test_cr1_reads_the_studio_schema_version_1() -> None:
    """Studio's ``studio.toml`` schema version 1 (STU-006) is a valid profile file: one format for both."""
    parsed = pf.parse(STUDIO_EXAMPLE)
    assert parsed.version == 1 and parsed.warnings == ()
    sqlite_profile, pg = parsed.profiles
    assert sqlite_profile == pf.ConnectionProfile(
        name="parts-dev", engine="sqlite", path="data/parts.db", environment="dev"
    )
    assert pg.password == pf.SecretRef("keyring") and str(pg.password) == "keyring:"
    assert (pg.host, pg.port, pg.database, pg.user, pg.sslmode) == (
        "db.example.internal",
        5432,
        "measurements",
        "studio_ro",
        "verify-full",
    )
    assert pg.read_only_role and pg.statement_timeout_ms == 15_000 and pg.mode == "read_only"
    assert parsed.get("parts-dev") is sqlite_profile
    with pytest.raises(KeyError):
        parsed.get("missing")


def test_cr1_doc14_fields() -> None:
    """Change request 14 section 3.1: mode, allowlist, schema fingerprint, every kind of secret reference."""
    text = (
        "version = 1\n"
        '[[profile]]\nname = "invoices"\nengine = "postgresql"\nhost = "10.0.0.5"\ndatabase = "billing"\n'
        'user = "nodestro"\npassword = "env:DB_INVOICES_PASSWORD"\nmode = "read_write"\n'
        'allow = ["public.invoices", "public.invoices.total_cents", "public.invoices"]\n'
        f'schema_fingerprint = "v1:sha256:{"a" * 64}"\napplication_name = "nodestro"\n'
        '[[profile]]\nname = "k"\nengine = "postgresql"\nhost = "/run/postgresql"\ndatabase = "d"\n'
        'password = "keyring:tabrion-studio/peter"\n'
        '[[profile]]\nname = "f"\nengine = "sqlite"\npath = "/srv/data/a.db"\npassword = "file:secrets/pw"\n'
        '[[profile]]\nname = "p"\nengine = "postgresql"\nhost = "::1"\ndatabase = "d"\npassword = "prompt"\n'
    )
    invoices, socket, file_profile, prompt = pf.parse(text).profiles
    assert invoices.password == pf.SecretRef("env", "DB_INVOICES_PASSWORD")
    assert invoices.mode == "read_write" and invoices.sslmode == "verify-full"  # a remote address
    assert invoices.allow == ("public.invoices", "public.invoices.total_cents")  # duplicates dropped
    assert invoices.schema_fingerprint == "v1:sha256:" + "a" * 64
    assert socket.password == pf.SecretRef("keyring", "tabrion-studio/peter")
    assert (socket.sslmode, socket.port) == ("prefer", None)  # a socket folder is local
    assert file_profile.password == pf.SecretRef("file", "secrets/pw")
    assert prompt.password == pf.SecretRef("prompt") and str(prompt.password) == "prompt"
    assert prompt.sslmode == "prefer"  # loopback
    assert invoices.url() == "postgresql+psycopg://nodestro@10.0.0.5:5432/billing?sslmode=verify-full"
    assert socket.url() == "postgresql+psycopg:///d?sslmode=prefer&host=/run/postgresql"
    assert prompt.url() == "postgresql+psycopg://[::1]:5432/d?sslmode=prefer"
    assert file_profile.url() == "sqlite:////srv/data/a.db"


@pytest.mark.parametrize(
    "password",
    [SECRET, f"pw:{SECRET}", f"{SECRET}:x", "keyring", "env", f"plain {SECRET}", f"ENV:{SECRET}", "Prompt"],
)
def test_cr1_plain_password_is_refused_and_never_repeated(password: str) -> None:
    text = _one(f'engine = "sqlite"\npath = "a.db"\npassword = "{password}"')
    with pytest.raises(pf.ProfileFileError) as info:
        pf.parse(text)
    issues = [(i.pointer, i.code) for i in info.value.issues]
    assert issues == [("profile[0].password", "profilefile.plain_password")]
    rendered = "".join(traceback.format_exception(info.value)) + repr(info.value) + str(info.value.issues)
    assert SECRET not in rendered
    assert info.value.__cause__ is None and info.value.__context__ is None
    with pytest.raises(pf.ProfileFileError) as direct:
        pf.SecretRef.parse(password)
    assert SECRET not in repr(direct.value) + str(direct.value.issues)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('engine = "sqlite"', [("profile[0].path", "profilefile.required")]),
        ('engine = "mysql"\npath = "a"', [("profile[0].engine", "profilefile.value")]),
        ('engine = "postgresql"\ndatabase = "d"', [("profile[0].host", "profilefile.required")]),
        ('engine = "postgresql"\nhost = "h"', [("profile[0].database", "profilefile.required")]),
        ('engine = "postgresql"\nhost = "h"\ndatabase = "d"\nport = 0', [("profile[0].port", "profilefile.range")]),
        ('engine = "postgresql"\nhost = "h"\ndatabase = "d"\nport = "5432"', [("profile[0].port", "profilefile.type")]),
        ('engine = "postgresql"\nhost = "h"\ndatabase = "d"\nport = true', [("profile[0].port", "profilefile.type")]),
        ('engine = "postgresql"\nhost = "u:p@h"\ndatabase = "d"', [("profile[0].host", "profilefile.value")]),
        ('engine = "postgresql"\nhost = "postgresql://h"\ndatabase = "d"', [("profile[0].host", "profilefile.value")]),
        (
            'engine = "postgresql"\nhost = "h"\ndatabase = "d"\nsslmode = "off"',
            [("profile[0].sslmode", "profilefile.value")],
        ),
        ('engine = "sqlite"\npath = "a"\nhost = "h"', [("profile[0].host", "profilefile.other_engine")]),
        (
            'engine = "postgresql"\nhost = "h"\ndatabase = "d"\npath = "a"',
            [("profile[0].path", "profilefile.other_engine")],
        ),
        ('engine = "sqlite"\npath = "a"\nmode = "write"', [("profile[0].mode", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a"\nenvironment = "staging"', [("profile[0].environment", "profilefile.value")]),
        (
            'engine = "sqlite"\npath = "a"\nstatement_timeout_ms = 0',
            [("profile[0].statement_timeout_ms", "profilefile.range")],
        ),
        ('engine = "sqlite"\npath = "a"\nread_only_role = "yes"', [("profile[0].read_only_role", "profilefile.type")]),
        ('engine = "sqlite"\npath = "a"\nallow = ["items"]', [("profile[0].allow[0]", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a"\nallow = ["a.b.c.d"]', [("profile[0].allow[0]", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a"\nallow = "main.items"', [("profile[0].allow", "profilefile.type")]),
        (
            'engine = "sqlite"\npath = "a"\nschema_fingerprint = "sha256:abc"',
            [("profile[0].schema_fingerprint", "profilefile.value")],
        ),
        ('engine = "sqlite"\npath = "a"\npassword = "env:1BAD"', [("profile[0].password", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a"\npassword = "keyring:svc"', [("profile[0].password", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a"\npassword = "file:"', [("profile[0].password", "profilefile.value")]),
        ('engine = "sqlite"\npath = " a"', [("profile[0].path", "profilefile.value")]),
        ('engine = "sqlite"\npath = "a\\u0000b"', [("profile[0].path", "profilefile.value")]),
    ],
)
def test_cr1_invalid_values(body: str, expected: list[tuple[str, str]]) -> None:
    assert _issues(_one(body)) == expected


@pytest.mark.parametrize(
    "path",
    [
        "//host/share/a.db",
        "\\\\host\\share\\a.db",
        "\\\\?\\C:\\a.db",
        "C:a.db",
        "file://host/a.db",
        "file:///a.db",
        "/net/h/a",
        "/Network/a",
        # review of #64, C-2: Studio's network_or_device cases (T-2, R-3)
        "\\??\\UNC\\host\\share\\a.db",
        "\\??\\C:\\a.db",
        "/??/C:/a.db",
        "/x/../net/host/a.db",
        "/./net/host/a.db",
        "\\net\\host\\a.db",
        "//./pipe/x",
        "FILE:a.db",
    ],
)
def test_cr1_network_paths_are_refused(path: str) -> None:
    escaped = path.replace("\\", "\\\\")
    assert _issues(_one(f'engine = "sqlite"\npath = "{escaped}"')) == [("profile[0].path", "profilefile.network_path")]
    assert _issues(_one(f'engine = "sqlite"\npath = "a"\npassword = "file:{escaped}"')) == [
        ("profile[0].password", "profilefile.network_path")
    ]


@pytest.mark.parametrize("path", ["a.db", "/srv/net/a.db", "/netx/a.db", "C:/data/a.db", "C:\\data\\a.db"])
def test_cr1_local_paths_are_kept(path: str) -> None:
    escaped = path.replace("\\", "\\\\")
    assert pf.parse(_one(f'engine = "sqlite"\npath = "{escaped}"')).profiles[0].path == path


def _frame_locals(error: BaseException) -> str:
    out = []
    tb = error.__traceback__
    while tb is not None:
        out.append(repr(tb.tb_frame.f_locals))
        tb = tb.tb_next
    return "".join(out)


def test_cr1_load_keeps_no_secret_in_the_frame_locals(tmp_path: Path) -> None:
    # review of #64, C-1: load() held the whole file (with the plain password) after parse() raised.
    path = tmp_path / "profiles.toml"
    path.write_text(_one(f'engine = "sqlite"\npath = "a.db"\npassword = "{SECRET}"'), encoding="utf-8")
    with pytest.raises(pf.ProfileFileError) as info:
        pf.load(path)
    assert [i.code for i in info.value.issues] == ["profilefile.plain_password"]
    assert SECRET not in _frame_locals(info.value)
    with pytest.raises(pf.ProfileFileError) as direct:
        pf.parse(path.read_bytes())
    assert SECRET not in _frame_locals(direct.value)


def test_cr1_dump_refuses_a_plain_value_in_password() -> None:
    # review of #64, L-1: a str placed in password by mistake is never written and not kept in dump's frame.
    misused = pf.ConnectionProfile(name="x", engine="sqlite", path="a.db", password=SECRET)  # type: ignore[arg-type]
    with pytest.raises(pf.ProfileFileError) as info:
        pf.dump([misused])
    assert [(i.pointer, i.code) for i in info.value.issues] == [("profile[0].password", "profilefile.type")]
    del misused
    assert SECRET not in _frame_locals(info.value) + repr(info.value)


def test_cr1_url_sqlite_path_and_brackets_once() -> None:
    # review of #64, L-2 and L-3 a.
    # "?" and "#" would start the URL's query or fragment, and SQLAlchemy 2.0 does not unquote the path: refused.
    for path in ("x.db?mode=rw", "x.db#f"):
        assert _issues(_one(f'engine = "sqlite"\npath = "{path}"')) == [("profile[0].path", "profilefile.value")]
    for database in ("a/b", "a?b", "a#b", "a%20b", "a@b"):
        assert _issues(_one(f'engine = "postgresql"\nhost = "h"\ndatabase = "{database}"')) == [
            ("profile[0].database", "profilefile.value")
        ]
    sqlalchemy = pytest.importorskip("sqlalchemy")
    url = pf.ConnectionProfile(name="x", engine="sqlite", path="a b/c.db").url()
    assert sqlalchemy.engine.make_url(url).database == "a b/c.db"
    spaced = pf.parse(_one('engine = "postgresql"\nhost = "h"\ndatabase = "my db"\nuser = "u@x"')).profiles[0]
    parsed = sqlalchemy.engine.make_url(spaced.url())
    assert (parsed.database, parsed.username) == ("my db", "u@x")  # the same on SQLAlchemy 2.0 and 2.1
    bracketed = pf.parse(_one('engine = "postgresql"\nhost = "[::1]"\ndatabase = "d"')).profiles[0]
    assert bracketed.sslmode == "prefer"
    assert bracketed.url() == "postgresql+psycopg://[::1]:5432/d?sslmode=prefer"


def test_cr1_file_level_rules() -> None:
    assert _issues('[[profile]]\nname = "x"\nengine = "sqlite"\npath = "a"\n') == [("version", "profilefile.required")]
    assert _issues("version = 2\n") == [("version", "profilefile.version")]
    assert _issues("version = true\n") == [("version", "profilefile.version")]
    assert _issues('version = 1\nprofile = "x"\n') == [("profile", "profilefile.type")]
    assert _issues('version = 1\n[[profile]]\nengine = "sqlite"\npath = "a"\n') == [
        ("profile[0].name", "profilefile.required")
    ]
    duplicate = "version = 1\n" + '[[profile]]\nname = "x"\nengine = "sqlite"\npath = "a"\n' * 2
    assert _issues(duplicate) == [("profile[1].name", "profilefile.duplicate")]
    assert _issues(b"version = 1\n\xff") == [("", "profilefile.encoding")]
    assert _issues(b"x" * (256 * 1024 + 1)) == [("", "profilefile.too_large")]
    assert pf.parse("version = 1\n").profiles == ()


def test_cr1_syntax_error_keeps_only_the_line() -> None:
    text = f'version = 1\n[[profile]]\nname = "x"\npassword = "{SECRET}\n'
    with pytest.raises(pf.ProfileFileError) as info:
        pf.parse(text)
    (issue,) = info.value.issues
    assert issue.code == "profilefile.syntax" and issue.pointer.startswith("line ")
    assert SECRET not in "".join(traceback.format_exception(info.value))
    assert info.value.__context__ is None


def test_cr1_unknown_keys_are_warnings_without_the_key() -> None:
    parsed = pf.parse(_one(f'engine = "sqlite"\npath = "a"\n{SECRET.replace("-", "_")} = 1') + "extra = 1\n")
    assert [(w.pointer, w.code, w.severity) for w in parsed.warnings] == [
        ("profile[0]", "profilefile.unknown_key", "warning"),
        ("profile[0]", "profilefile.unknown_key", "warning"),
    ]
    assert "SECRET" not in repr(parsed.warnings)


def test_cr1_every_error_is_reported_at_once() -> None:
    text = 'version = 1\n[[profile]]\nname = "a"\nengine = "sqlite"\n[[profile]]\nname = "b"\nengine = "x"\n'
    assert _issues(text) == [("profile[0].path", "profilefile.required"), ("profile[1].engine", "profilefile.value")]


def test_cr1_dump_round_trip() -> None:
    profiles = (
        *pf.parse(STUDIO_EXAMPLE).profiles,
        pf.ConnectionProfile(
            name='quote " back \\ and\u2028line',
            engine="postgresql",
            host="db",
            database="d",
            password=pf.SecretRef("env", "PW"),
            allow=("public.t",),
            description="tab\tsep",
            schema_fingerprint="v1:sha256:" + "0" * 64,
            mode="read_write",
        ),
    )
    with pytest.raises(pf.ProfileFileError):  # a control character in a name is refused on reading
        pf.dump(profiles)
    profiles = (*profiles[:2], pf.ConnectionProfile(name='quote " back \\', engine="sqlite", path="a b/c.db"))
    text = pf.dump(profiles)
    assert pf.parse(text).profiles == profiles
    assert pf.dump(pf.parse(text).profiles) == text
    assert "\u2028" not in pf.dump([pf.ConnectionProfile(name="n", engine="sqlite", path="a")])


def test_cr1_dump_refuses_what_parse_refuses() -> None:
    bad = pf.ConnectionProfile(name="x", engine="postgresql", host="h")  # no database
    with pytest.raises(pf.ProfileFileError) as info:
        pf.dump([bad])
    assert ("profile[0].database", "profilefile.required") in [(i.pointer, i.code) for i in info.value.issues]


def test_cr1_load_reads_a_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(STUDIO_EXAMPLE, encoding="utf-8")
    assert len(pf.load(path).profiles) == 2
    assert len(pf.load(str(path)).profiles) == 2


def test_cr1_load_refusals(tmp_path: Path) -> None:
    def code(path: Path) -> str:
        with pytest.raises(pf.ProfileFileError) as info:
            pf.load(path)
        (issue,) = info.value.issues
        assert str(tmp_path) not in "".join(traceback.format_exception(info.value))
        return issue.code

    assert code(tmp_path / "missing.toml") == "profilefile.unreadable"
    assert code(tmp_path) == "profilefile.not_regular_file"
    big = tmp_path / "big.toml"
    big.write_bytes(b"#" * (256 * 1024 + 1))
    assert code(big) == "profilefile.too_large"
    if sys.platform != "win32":
        target = tmp_path / "real.toml"
        target.write_text(STUDIO_EXAMPLE, encoding="utf-8")
        link = tmp_path / "link.toml"
        os.symlink(target, link)
        assert code(link) == "profilefile.symlink"
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        assert code(fifo) == "profilefile.not_regular_file"


def test_cr1_deep_nesting_is_a_syntax_issue() -> None:
    text = "version = 1\nx = " + "[" * 100_000 + "]" * 100_000 + "\n"
    try:
        parsed = pf.parse(text)
    except pf.ProfileFileError as exc:
        assert [i.code for i in exc.issues] == ["profilefile.syntax"]
    else:  # a parser that copes with the depth: the unknown key is only a warning
        assert [w.code for w in parsed.warnings] == ["profilefile.unknown_key"]


def test_cr1_type_of_input() -> None:
    assert _issues(42) == [("", "profilefile.type")]  # type: ignore[arg-type]
