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
    query = rest.find("?")
    head = rest if query < 0 else rest[:query]
    at = head.rfind("@")
    if at < 0 and query >= 0:
        # No user information before the query. An "@" later that is not inside a parameter value (no "=" before it)
        # means a password with a raw "?" in it: refuse rather than guess.
        tail = rest[query + 1 :]
        later = tail.find("@")
        if later >= 0 and "=" not in tail[:later] and ":" in head:
            return None
    if at >= 0:
        userinfo = head[:at]
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


def _read_file(variable: str, path: str) -> str:
    """The content of the regular file ``path`` without its trailing newline (symbolic links are followed)."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        raise ConfigurationError("config.unreadable", details={"name": variable}) from None
    try:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ConfigurationError("config.not_regular_file", details={"name": variable})
            data = handle.read()
    except OSError:
        raise ConfigurationError("config.unreadable", details={"name": variable}) from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("config.unreadable", details={"name": variable}) from None
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    return text


def url_from_env(name: str, *, environ: Mapping[str, str] | None = None, required: bool = True) -> str | None:
    """Read a URL from ``name`` or from the file named by ``name + "_FILE"``.

    A file must be a regular file (symlinks allowed); its content without the trailing newline counts. Both set:
    ``ConfigurationError("config.ambiguous")``; neither set and ``required``: ``ConfigurationError("config.missing")``.
    No message contains a value.

    An empty variable counts as not set, and so does an empty file. A file that cannot be read or is not valid UTF-8
    raises ``ConfigurationError("config.unreadable")``, one that is not a regular file (a directory, a FIFO, a
    device) ``ConfigurationError("config.not_regular_file")``. The details name the variable, never its value.
    """
    env = os.environ if environ is None else environ
    file_variable = name + "_FILE"
    direct = env.get(name) or None
    path = env.get(file_variable) or None
    if direct is not None and path is not None:
        raise ConfigurationError("config.ambiguous", details={"name": name})
    value = direct if direct is not None else _read_file(file_variable, path) if path is not None else None
    if not value:
        if required:
            raise ConfigurationError("config.missing", details={"name": name})
        return None
    return value
