"""Connection-URL helpers (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 2.3. :func:`safe_url` hides passwords in connection strings for logs,
``repr`` and error messages; :func:`url_from_env` reads a connection string from the environment or from a secret
file (``*_FILE``, as Nodestro's ``server_settings.py`` does). Neither ever puts a value into an error message.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import re
import stat

from tabrion.errors import ConfigurationError

__all__ = ["safe_url", "url_from_env"]

_MASK = "***"
_UNPARSEABLE = "<unparseable url>"
_URL = re.compile(r"\s*[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_KEYS = frozenset({"password", "sslpassword"})
"""Keywords (conninfo) and query parameters whose values are masked."""
_QUERY_PARAMETER = re.compile(r"(?i)([?&](?:ssl)?password=)[^&#]*")


def _mask_url(url: str, scheme_end: int) -> str | None:
    rest = url[scheme_end:]
    # libpq ends the user information at the last "@" before the first "/" (review M-1: a raw "?" or "&" in a
    # password); a raw "/" in a password is covered by also taking the last "@" before the query. The later of the two
    # wins, so more is hidden rather than less.
    slash = rest.find("/")
    query = rest.find("?")
    libpq_head = rest if slash < 0 else rest[:slash]
    head = rest if query < 0 else rest[:query]
    at = max(libpq_head.rfind("@"), head.rfind("@"))
    if at < 0 and query >= 0:
        # No user information found. An "@" later that is not inside a parameter value (no "=" before it) and a ":"
        # before the query mean a password with raw "/" and "?" in it: refuse rather than guess.
        tail = rest[query + 1 :]
        later = tail.find("@")
        if later >= 0 and "=" not in tail[:later] and ":" in head:
            return None
    if at >= 0:
        userinfo = rest[:at]
        colon = userinfo.find(":")
        if colon >= 0:
            rest = userinfo[: colon + 1] + _MASK + rest[at:]
    return url[:scheme_end] + _QUERY_PARAMETER.sub(lambda m: m.group(1) + _MASK, rest)


def _mask_conninfo(text: str) -> str | None:
    """Mask ``password=...`` in a libpq keyword/value string; ``None`` when it is not one."""
    out: list[str] = []
    i, n, last = 0, len(text), 0
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        key_start = i
        while i < n and not text[i].isspace() and text[i] != "=":
            i += 1
        key = text[key_start:i]
        while i < n and text[i].isspace():
            i += 1
        if not key or i >= n or text[i] != "=":
            return None
        i += 1
        while i < n and text[i].isspace():
            i += 1
        value_start = i
        if i < n and text[i] == "'":
            i += 1
            while i < n and text[i] != "'":
                i += 2 if text[i] == "\\" else 1
            if i >= n:
                return None  # unterminated quote
            i += 1
        else:
            while i < n and not text[i].isspace():
                i += 2 if text[i] == "\\" else 1
            i = min(i, n)
        if key.lower() in _SECRET_KEYS:
            out += [text[last:value_start], _MASK]
            last = i
    out.append(text[last:])
    return "".join(out)


def safe_url(url: str) -> str:
    """Mask the password of a URL or keyword conninfo (``postgresql://u:***@h/db``, ``password=***``).

    Query parameters named ``password`` are masked as well. An unparseable input returns ``"<unparseable url>"``.
    Never raises (TAB-T-C01..C08).
    """
    try:
        if not isinstance(url, str):
            return _UNPARSEABLE
        match = _URL.match(url)
        masked = _mask_url(url, match.end()) if match else _mask_conninfo(url)
        return _UNPARSEABLE if masked is None else masked
    except Exception:
        return _UNPARSEABLE


_MAX_FILE_BYTES = 64 * 1024
"""A connection string is short; a larger file (or a procfs file) is refused, not read (review L-6)."""


def _read_regular_file(path: str) -> tuple[bytes | None, str | None]:
    """``(content, None)`` or ``(None, error code)``; the file descriptor is always closed (review L-1)."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        return None, "config.unreadable"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "config.not_regular_file"
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(fd, 16 * 1024):
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                return None, "config.too_large"
            chunks.append(chunk)
        return b"".join(chunks), None
    except OSError:
        return None, "config.unreadable"
    finally:
        os.close(fd)


def _read_file(variable: str, path: str) -> str:
    """The content of the regular file ``path`` without its trailing newline (symbolic links are followed).

    Every error is raised outside an ``except`` block, so it carries no ``__context__`` with the path or the file's
    bytes (review L-3).
    """
    data, code = _read_regular_file(path)
    text: str | None = None
    if data is not None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            code = "config.unreadable"
    if text is None:
        del data, path  # no content or path in the frame locals (I-76)
        raise ConfigurationError(code or "config.unreadable", details={"name": variable})
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    return text


def _lookup(env: Mapping[str, str], name: str, file_variable: str) -> tuple[str | None, str | None]:
    return env.get(name) or None, env.get(file_variable) or None


def url_from_env(name: str, *, environ: Mapping[str, str] | None = None, required: bool = True) -> str | None:
    """Read a URL from ``name`` or from the file named by ``name + "_FILE"``.

    A file must be a regular file (symlinks allowed); its content without the trailing newline counts. Both set:
    ``ConfigurationError("config.ambiguous")``; neither set and ``required``: ``ConfigurationError("config.missing")``.
    No message contains a value.

    An empty variable counts as not set, and so does an empty file. A file that cannot be read or is not valid UTF-8
    raises ``ConfigurationError("config.unreadable")``, one that is not a regular file (a directory, a FIFO, a
    device) ``ConfigurationError("config.not_regular_file")``, one larger than 64 KiB
    ``ConfigurationError("config.too_large")``. The details name the variable, never its value or the path.
    """
    file_variable = name + "_FILE"
    direct, path = _lookup(os.environ if environ is None else environ, name, file_variable)
    # The frames of a traceback keep their locals, which Sentry, ``rich`` or ``pytest -l`` show: neither the
    # environment nor a value stays in them when an error is raised (I-76).
    del environ
    if direct is not None and path is not None:
        direct = path = None
        raise ConfigurationError("config.ambiguous", details={"name": name})
    try:
        value = direct if direct is not None else _read_file(file_variable, path) if path is not None else None
    except BaseException:
        direct = path = None
        raise
    if not value:
        if required:
            raise ConfigurationError("config.missing", details={"name": name})
        return None
    return value
