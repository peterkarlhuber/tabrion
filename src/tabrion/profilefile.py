"""Connection profile file format v1: parse, check and write; no storage, no secrets (TAB-CR-1, CR-DB).

Contract: ``docs/api/contract.md`` section 5.9 and implementation note I-109. Standard library only (``tomllib``).

A profile file names databases without holding a secret: the password is a *reference* (``env:NAME``, ``keyring:``
or ``keyring:<service>/<user>``, ``file:PATH``, ``prompt``) that the consumer resolves (Tabrion Studio, Nodestro);
a plain password is refused and never repeated. Every issue is ``(pointer, code, severity)`` with a key path such as
``profile[2].port``, never a value. The format fixes no file name (R-CR-6); a consumer may choose one.

Every error is raised outside an ``except`` block, so it has neither ``__cause__`` nor ``__context__`` with the
file's text (I-39), and no value or path stays in the frame locals of the traceback (I-76).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import contextlib
from dataclasses import dataclass, field
import ipaddress
import math
import os
import posixpath
import re
import stat
import tomllib
from typing import Any, Literal
from urllib.parse import quote

from tabrion.errors import ConfigurationError

__all__ = [
    "FORMAT_VERSION",
    "ConnectionProfile",
    "ProfileFile",
    "ProfileFileError",
    "ProfileIssue",
    "SecretRef",
    "dump",
    "load",
    "parse",
]

FORMAT_VERSION = 1
"""The only version this module reads and writes."""

Engine = Literal["sqlite", "postgresql"]
Mode = Literal["read_only", "read_write"]
Environment = Literal["dev", "test", "prod"]
SecretKind = Literal["env", "keyring", "file", "prompt"]
Severity = Literal["error", "warning"]

_MAX_BYTES = 256 * 1024
_MAX_PROFILES = 1_000
_MAX_NAME = 200
_MAX_TEXT = 1_024
_MAX_ALLOW = 10_000
_MAX_TIMEOUT_MS = 2_147_483_647
_ENGINES = ("sqlite", "postgresql")
_MODES = ("read_only", "read_write")
_ENVIRONMENTS = ("dev", "test", "prod")
_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}")
_IDENTIFIER = re.compile(r"[^\x00-\x1f\x7f.\s][^\x00-\x1f\x7f.]{0,62}")
_FINGERPRINT = re.compile(r"v1:sha256:[0-9a-f]{64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")
_DRIVE_RELATIVE = re.compile(r"[A-Za-z]:(?![\\/])")
_SEPARATORS = "/\\"
_AUTOMOUNT = ("/net", "/network")
_TOML_LINE = re.compile(r"line (\d+)")

_COMMON_KEYS = frozenset(
    {
        "name",
        "engine",
        "password",
        "environment",
        "mode",
        "read_only_role",
        "statement_timeout_ms",
        "allow",
        "schema_fingerprint",
        "application_name",
        "description",
    }
)
_SQLITE_KEYS = frozenset({"path"})
_POSTGRES_KEYS = frozenset({"host", "port", "database", "user", "sslmode"})
_KEY_ORDER = (
    "name",
    "description",
    "engine",
    "path",
    "host",
    "port",
    "database",
    "user",
    "password",
    "sslmode",
    "environment",
    "mode",
    "read_only_role",
    "statement_timeout_ms",
    "application_name",
    "allow",
    "schema_fingerprint",
)


@dataclass(frozen=True, slots=True)
class ProfileIssue:
    """One finding: a key path (``profile[0].port``), a stable code and its severity. Never a value."""

    pointer: str
    code: str
    severity: Severity = "error"


class ProfileFileError(ConfigurationError):
    """``code == "profilefile.invalid"``; ``issues`` lists every error (and warning) found, without values."""

    issues: tuple[ProfileIssue, ...]

    def __init__(self, issues: Sequence[ProfileIssue]) -> None:
        errors = sum(1 for issue in issues if issue.severity == "error")
        super().__init__("profilefile.invalid", details={"errors": errors})
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Where the consumer finds the password; ``str()`` gives the reference as written in the file.

    ``env``: ``target`` is the variable name. ``keyring``: ``target`` is ``"<service>/<user>"`` or ``None`` (the
    consumer's default entry, written ``keyring:``). ``file``: ``target`` is the path. ``prompt``: ``target`` is
    ``None``; the consumer asks the user."""

    kind: SecretKind
    target: str | None = None

    def __str__(self) -> str:
        if self.kind == "prompt":
            return "prompt"
        return f"{self.kind}:{self.target or ''}"

    @classmethod
    def parse(cls, text: str) -> SecretRef:
        """The reference ``text`` (``env:NAME``, ``keyring:``, ``keyring:svc/user``, ``file:PATH``, ``prompt``).

        Anything else raises :class:`ProfileFileError` with ``profilefile.plain_password``; the text is never
        repeated."""
        ref, code = _secret(text)
        if ref is None:
            del text  # I-76
            raise ProfileFileError([ProfileIssue("password", code)])
        return ref


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    """One connection profile of format v1. Holds no secret; ``password`` is a reference or ``None``."""

    name: str
    engine: Engine
    path: str | None = None
    """SQLite: the database file; a relative path is relative to the profile file's folder (the consumer resolves)."""
    host: str | None = None
    """PostgreSQL: a host name, an IP address or a Unix socket folder (``/...``)."""
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: SecretRef | None = None
    sslmode: str | None = None
    """PostgreSQL; when the file leaves it out: ``prefer`` for a socket folder, ``localhost`` and a loopback address,
    else ``verify-full``."""
    environment: Environment | None = None
    mode: Mode = "read_only"
    read_only_role: bool = False
    """The profile's role is meant to have no write rights (a promise for the reviewer, not a check)."""
    statement_timeout_ms: int | None = None
    application_name: str | None = None
    allow: tuple[str, ...] = ()
    """Allowlist: ``schema.table`` or ``schema.table.column`` (SQLite: ``main.table``); empty means nothing."""
    schema_fingerprint: str | None = None
    """``v1:sha256:<hex>`` of :func:`tabrion.schema.fingerprint` (TAB-CR-2), or ``None``."""
    description: str | None = None

    def url(self) -> str:
        """A SQLAlchemy URL without any password (``sqlite:///path``, ``postgresql+psycopg://user@host:port/db``).

        The consumer resolves :attr:`password` and adds it (``sqlalchemy.engine.make_url(url).set(password=...)``)."""
        if self.engine == "sqlite":
            return f"sqlite:///{quote(self.path or '', safe='/:')}"  # "?" and "#" stay part of the path
        user = f"{quote(self.user, safe='')}@" if self.user else ""
        host = self.host or ""
        query = f"?sslmode={self.sslmode}" if self.sslmode else ""
        if host.startswith("/"):
            query += ("&" if query else "?") + "host=" + quote(host, safe="/")
            host = ""
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{self.port}" if self.port is not None and host else ""
        return f"postgresql+psycopg://{user}{host}{port}/{quote(self.database or '', safe='')}{query}"


@dataclass(frozen=True, slots=True)
class ProfileFile:
    """A parsed file: its profiles in file order and its warnings (unknown keys)."""

    profiles: tuple[ConnectionProfile, ...]
    warnings: tuple[ProfileIssue, ...] = field(default=())
    version: int = FORMAT_VERSION

    def get(self, name: str) -> ConnectionProfile:
        """The profile named ``name``; ``KeyError`` (with the name, which is no secret) if there is none."""
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise KeyError(name)


# ---------------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------------


def parse(text: str | bytes) -> ProfileFile:
    """Parse and check a profile file of format v1 (TOML, UTF-8).

    Raises :class:`ProfileFileError` with every issue when the file has an error; warnings (unknown keys) are in
    :attr:`ProfileFile.warnings`. A TOML syntax error keeps only its line number."""
    issues: list[ProfileIssue] = []
    document = _toml(text, issues)
    del text  # I-76
    profiles: list[ConnectionProfile] = []
    if document is not None:
        profiles = _document(document, issues)
    document = None
    if any(issue.severity == "error" for issue in issues):
        profiles = []
        raise ProfileFileError(issues)
    return ProfileFile(profiles=tuple(profiles), warnings=tuple(issues))


def load(path: str | os.PathLike[str]) -> ProfileFile:
    """Read and :func:`parse` the file at ``path``.

    The file must be a regular file of at most 256 KiB, and ``path`` itself must not be a symbolic link (a profile
    file may come from an untrusted repository). Errors: ``ProfileFileError`` with ``profilefile.unreadable``,
    ``profilefile.not_regular_file``, ``profilefile.symlink`` or ``profilefile.too_large`` (pointer ``""``)."""
    data, code = _read(os.fspath(path))
    del path  # I-76
    if data is None:
        raise ProfileFileError([ProfileIssue("", code)])
    try:
        return parse(data)
    finally:
        data = None  # the whole file, perhaps with a plain password: not in the traceback's frame locals (I-76)


def _read(path: str) -> tuple[bytes | None, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return None, "profilefile.symlink"
        fd = os.open(path, flags)
    except (OSError, ValueError):
        return None, "profilefile.unreadable"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "profilefile.not_regular_file"
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(fd, 64 * 1024):
            size += len(chunk)
            if size > _MAX_BYTES:
                return None, "profilefile.too_large"
            chunks.append(chunk)
        return b"".join(chunks), ""
    except OSError:
        return None, "profilefile.unreadable"
    finally:
        os.close(fd)


def _toml(text: str | bytes, issues: list[ProfileIssue]) -> dict[str, Any] | None:
    if isinstance(text, bytes):
        if len(text) > _MAX_BYTES:
            issues.append(ProfileIssue("", "profilefile.too_large"))
            return None
        decoded: str | None = None
        with contextlib.suppress(UnicodeDecodeError):
            decoded = text.decode("utf-8")
        if decoded is None:
            issues.append(ProfileIssue("", "profilefile.encoding"))
            return None
        text = decoded
    if not isinstance(text, str):
        issues.append(ProfileIssue("", "profilefile.type"))
        return None
    if len(text.encode("utf-8", "surrogatepass")) > _MAX_BYTES:
        issues.append(ProfileIssue("", "profilefile.too_large"))
        return None
    line: str | None = None
    document: dict[str, Any] | None = None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        found = _TOML_LINE.search(str(exc))
        line = found.group(1) if found else "?"
    except RecursionError:
        line = "?"
    if document is None:
        # Only the line number: the parser's message may quote the file's text.
        issues.append(ProfileIssue(f"line {line}", "profilefile.syntax"))
    return document


def _document(document: Mapping[str, Any], issues: list[ProfileIssue]) -> list[ConnectionProfile]:
    version = document.get("version")
    if version is None:
        issues.append(ProfileIssue("version", "profilefile.required"))
        return []
    if isinstance(version, bool) or not isinstance(version, int) or version != FORMAT_VERSION:
        issues.append(ProfileIssue("version", "profilefile.version"))
        return []
    for key in document:
        if key not in ("version", "profile"):
            issues.append(ProfileIssue("", "profilefile.unknown_key", "warning"))
    tables = document.get("profile", [])
    if not isinstance(tables, list):
        issues.append(ProfileIssue("profile", "profilefile.type"))
        return []
    if len(tables) > _MAX_PROFILES:
        issues.append(ProfileIssue("profile", "profilefile.too_many"))
        return []
    profiles: list[ConnectionProfile] = []
    names: set[str] = set()
    for index, table in enumerate(tables):
        pointer = f"profile[{index}]"
        if not isinstance(table, dict):
            issues.append(ProfileIssue(pointer, "profilefile.type"))
            continue
        profile = _profile(table, pointer, issues)
        if profile is None:
            continue
        if profile.name in names:
            issues.append(ProfileIssue(f"{pointer}.name", "profilefile.duplicate"))
        names.add(profile.name)
        profiles.append(profile)
    return profiles


def _profile(table: Mapping[str, Any], pointer: str, issues: list[ProfileIssue]) -> ConnectionProfile | None:
    before = len([i for i in issues if i.severity == "error"])
    reader = _Reader(table, pointer, issues)
    name = reader.text("name", required=True, limit=_MAX_NAME)
    engine = reader.choice("engine", _ENGINES, required=True)
    own = _SQLITE_KEYS if engine == "sqlite" else _POSTGRES_KEYS if engine == "postgresql" else frozenset()
    other = (_SQLITE_KEYS | _POSTGRES_KEYS) - own
    for key in table:
        if key in other and engine is not None:
            issues.append(ProfileIssue(f"{pointer}.{key}", "profilefile.other_engine"))
        elif key not in _COMMON_KEYS and key not in own and key not in other:
            # The key itself may be text an attacker chose: not repeated (pointer names the table only).
            issues.append(ProfileIssue(pointer, "profilefile.unknown_key", "warning"))
    path = host = database = user = sslmode = None
    port: int | None = None
    if engine == "sqlite":
        path = reader.location("path", required=True)
    elif engine == "postgresql":
        host = reader.host("host")
        port = reader.integer("port", 1, 65_535)
        database = reader.text("database", required=True, limit=_MAX_TEXT)
        user = reader.text("user", limit=_MAX_TEXT)
        sslmode = reader.choice("sslmode", _SSLMODES)
        if host is not None and sslmode is None:
            sslmode = "prefer" if _is_local(host) else "verify-full"
        if port is None and host is not None and not host.startswith("/"):
            port = 5432
    password = reader.secret("password")
    environment = reader.choice("environment", _ENVIRONMENTS)
    mode = reader.choice("mode", _MODES) or "read_only"
    read_only_role = reader.boolean("read_only_role")
    timeout = reader.integer("statement_timeout_ms", 1, _MAX_TIMEOUT_MS)
    application_name = reader.text("application_name", limit=63)
    description = reader.text("description", limit=_MAX_TEXT)
    allow = reader.allow("allow")
    fingerprint = reader.pattern("schema_fingerprint", _FINGERPRINT)
    if engine == "postgresql" and table.get("host") is None:
        issues.append(ProfileIssue(f"{pointer}.host", "profilefile.required"))
    if len([i for i in issues if i.severity == "error"]) > before or name is None or engine is None:
        return None
    return ConnectionProfile(
        name=name,
        engine=engine,  # type: ignore[arg-type]
        path=path,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        sslmode=sslmode,
        environment=environment,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        read_only_role=bool(read_only_role),
        statement_timeout_ms=timeout,
        application_name=application_name,
        allow=allow,
        schema_fingerprint=fingerprint,
        description=description,
    )


class _Reader:
    """Typed access to one profile table; each problem becomes an issue with a key path, never a value."""

    def __init__(self, table: Mapping[str, Any], pointer: str, issues: list[ProfileIssue]) -> None:
        self._table = table
        self._pointer = pointer
        self._issues = issues

    def _issue(self, key: str, code: str) -> None:
        self._issues.append(ProfileIssue(f"{self._pointer}.{key}", code))

    def _get(self, key: str, required: bool, kind: type | tuple[type, ...]) -> Any:
        value = self._table.get(key)
        if value is None:
            if required:
                self._issue(key, "profilefile.required")
            return None
        if (isinstance(value, bool) and kind is not bool) or not isinstance(value, kind):
            self._issue(key, "profilefile.type")
            return None
        return value

    def text(self, key: str, *, required: bool = False, limit: int) -> str | None:
        value = self._get(key, required, str)
        if value is None:
            return None
        if not value or len(value) > limit or _CONTROL.search(value) or value != value.strip():
            self._issue(key, "profilefile.value")
            return None
        return str(value)

    def choice(self, key: str, choices: tuple[str, ...], *, required: bool = False) -> str | None:
        value = self._get(key, required, str)
        if value is not None and value not in choices:
            self._issue(key, "profilefile.value")
            return None
        return value  # type: ignore[no-any-return]

    def integer(self, key: str, low: int, high: int) -> int | None:
        value = self._get(key, False, int)
        if value is not None and not low <= value <= high:
            self._issue(key, "profilefile.range")
            return None
        return value  # type: ignore[no-any-return]

    def boolean(self, key: str) -> bool | None:
        return self._get(key, False, bool)  # type: ignore[no-any-return]

    def location(self, key: str, *, required: bool) -> str | None:
        value = self.text(key, required=required, limit=4_096)
        if value is not None and _is_network_path(value):
            self._issue(key, "profilefile.network_path")
            return None
        return value

    def host(self, key: str) -> str | None:
        value = self.text(key, limit=255)
        if value is None:
            return None
        if value.startswith("/"):
            return value
        if any(char in value for char in "@/?#\\ ,") or "://" in value:
            self._issue(key, "profilefile.value")  # a URL or credentials in the host field
            return None
        return value

    def secret(self, key: str) -> SecretRef | None:
        value = self._get(key, False, str)
        if value is None:
            return None
        ref, code = _secret(value)
        del value  # I-76
        if ref is None:
            self._issue(key, code)
        return ref

    def allow(self, key: str) -> tuple[str, ...]:
        value = self._get(key, False, list)
        if value is None:
            return ()
        if len(value) > _MAX_ALLOW:
            self._issue(key, "profilefile.too_many")
            return ()
        out: list[str] = []
        for index, item in enumerate(value):
            parts = item.split(".") if isinstance(item, str) else []
            if not 2 <= len(parts) <= 3 or not all(_IDENTIFIER.fullmatch(part) for part in parts):
                self._issues.append(ProfileIssue(f"{self._pointer}.{key}[{index}]", "profilefile.value"))
                continue
            out.append(item)
        return tuple(dict.fromkeys(out))

    def pattern(self, key: str, regex: re.Pattern[str]) -> str | None:
        value = self._get(key, False, str)
        if value is not None and not regex.fullmatch(value):
            self._issue(key, "profilefile.value")
            return None
        return value  # type: ignore[no-any-return]


def _secret(text: str) -> tuple[SecretRef | None, str]:
    if text == "prompt":
        return SecretRef("prompt"), ""
    kind, colon, target = text.partition(":")
    if not colon or kind not in ("env", "keyring", "file"):
        return None, "profilefile.plain_password"
    if kind == "env":
        if _ENV_NAME.fullmatch(target):
            return SecretRef("env", target), ""
        return None, "profilefile.value"
    if kind == "keyring":
        if not target:
            return SecretRef("keyring"), ""
        service, slash, user = target.partition("/")
        if slash and service and user and not _CONTROL.search(target) and len(target) <= _MAX_TEXT:
            return SecretRef("keyring", target), ""
        return None, "profilefile.value"
    if not target or _CONTROL.search(target) or len(target) > 4_096:
        return None, "profilefile.value"
    if _is_network_path(target):
        return None, "profilefile.network_path"
    return SecretRef("file", target), ""


def _is_network_path(text: str) -> bool:
    """A path that names another host, a device or a drive-relative path (text only; the same on every platform).

    The same rules as Tabrion Studio's ``network_or_device`` (Studio review T-2, re-review R-3), and a ``file:`` URL
    is refused outright."""
    if text[:5].lower() == "file:":
        return True
    if len(text) >= 2 and text[0] in _SEPARATORS and text[1] in _SEPARATORS:
        return True  # \\host\share, //host/share, \\?\..., \\.\...
    if len(text) >= 4 and text[0] in _SEPARATORS and text[1:3] == "??" and text[3] in _SEPARATORS:
        return True  # \??\..., the NT namespace prefix that Win32 passes on unchanged (\??\UNC\host\share)
    if _DRIVE_RELATIVE.match(text) is not None:
        return True  # C:x is relative to the current folder of drive C
    if text[:1] in _SEPARATORS:
        folded = posixpath.normpath(text.replace("\\", "/")).lower()
        if any(folded == folder or folded.startswith(folder + "/") for folder in _AUTOMOUNT):
            return True  # /net/host and /Network/... reach another host through the automounter
    return False


def _is_local(host: str) -> bool:
    if host.startswith("/") or host.lower() == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------------------------------


def dump(profiles: Iterable[ConnectionProfile]) -> str:
    """The profile file of ``profiles`` (format v1); :func:`parse` reads it back to equal profiles.

    Raises :class:`ProfileFileError` if a profile would not pass :func:`parse` (the check runs on the written text,
    so what ``dump`` returns always parses)."""
    profiles = tuple(profiles)
    misused = next(
        (index for index, p in enumerate(profiles) if p.password is not None and not isinstance(p.password, SecretRef)),
        None,
    )
    if misused is not None:
        profiles = ()  # a plain value in ``password`` is never written and stays out of the frame locals (I-76)
        raise ProfileFileError([ProfileIssue(f"profile[{misused}].password", "profilefile.type")])
    lines = [f"version = {FORMAT_VERSION}"]
    for profile in profiles:
        lines += ["", "[[profile]]"]
        values = {
            "name": profile.name,
            "description": profile.description,
            "engine": profile.engine,
            "path": profile.path,
            "host": profile.host,
            "port": profile.port,
            "database": profile.database,
            "user": profile.user,
            "password": None if profile.password is None else str(profile.password),
            "sslmode": profile.sslmode,
            "environment": profile.environment,
            "mode": profile.mode,
            "read_only_role": profile.read_only_role or None,
            "statement_timeout_ms": profile.statement_timeout_ms,
            "application_name": profile.application_name,
            "allow": list(profile.allow) if profile.allow else None,
            "schema_fingerprint": profile.schema_fingerprint,
        }
        for key in _KEY_ORDER:
            value = values[key]
            if value is not None:
                lines.append(f"{key} = {_toml_value(value)}")
    text = "\n".join(lines) + "\n"
    lines = []
    try:
        parsed = parse(text)
    except ProfileFileError:
        text = ""  # I-76: neither the written text nor the values stay in the frame locals
        values = {}
        raise
    if parsed.profiles != profiles:
        raise ProfileFileError([ProfileIssue("", "profilefile.not_storable")])
    return text


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, str):
        return '"' + "".join(_toml_char(char) for char in value) + '"'
    raise ProfileFileError([ProfileIssue("", "profilefile.type")])


def _toml_char(char: str) -> str:
    if char == '"':
        return '\\"'
    if char == "\\":
        return "\\\\"
    if _CONTROL.fullmatch(char):
        return f"\\u{ord(char):04x}"
    return char
