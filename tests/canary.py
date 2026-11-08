"""Canary helpers of the value-leak suite (QA-2, TAB-T-110..113; plan 07 TM-02, SR-05, SR-06). Standard library only.

A canary is a unique value (``tabrion-canary-<uuid>``) that a test puts where a secret or a data value goes: a bind
parameter, a row value in a PostgreSQL ``DETAIL``, a password, a token, a file's content. The helpers render an
exception every way Python and ``logging`` render one and fail when the canary appears in any rendering:

- :func:`assert_clean` for an error **Tabrion raises** (``TabrionError``): ``str``, ``repr``, ``args``, ``details``,
  ``classification``, ``__notes__``, ``traceback.format_exception`` and ``print_exception`` (what reaches stderr for an
  uncaught error), a plain ``logging`` handler **without** :class:`~tabrion.errors.DbErrorRedactionFilter`, and a
  pickle round trip. ``__cause__`` is ``None``; a ``__context__`` is only allowed when it is suppressed and named by
  the caller (contract I-39: the caller's own error from ``Transaction.connection``).
- :func:`assert_redacted` for a **driver error** that Tabrion classifies but does not raise (layer A passes driver
  errors through): ``safe_message``, ``repr(classify(...))`` and a ``logging`` handler with the filter, also for the
  error wrapped in a caller's exception. ``leaks=True`` is the positive control: the canary must be in the raw
  traceback, so the test proves the value reached the error and Tabrion removed it.
"""

from __future__ import annotations

from collections.abc import Iterable
import io
import logging
import pickle
import traceback
from typing import Any
import uuid

from tabrion.errors import DbErrorRedactionFilter, TabrionError, classify, safe_message


def new_canary(label: str = "") -> str:
    """A fresh canary; ``label`` tells several apart in a failure message."""
    return f"tabrion-canary-{label}{uuid.uuid4().hex}"


def _format(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _stderr(exc: BaseException) -> str:
    buffer = io.StringIO()
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=buffer)
    return buffer.getvalue()


class _RecordFormatter(logging.Formatter):
    """The standard format plus what a JSON formatter would add: the record's fields after the filters ran."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        extra = {k: v for k, v in vars(record).items() if k not in ("exc_info", "exc_text", "msg", "args")}
        return f"{text}\nmsg={record.msg!r} args={record.args!r} fields={extra!r}"


def logged(exc: BaseException, *, redact: bool) -> str:
    """What a handler writes for ``log.exception(...)`` with ``exc`` as argument and as ``exc_info``."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_RecordFormatter("%(levelname)s %(name)s %(message)s"))
    if redact:
        handler.addFilter(DbErrorRedactionFilter())
    log = logging.getLogger(f"tabrion-canary-suite.{uuid.uuid4().hex}")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    try:
        log.error("operation failed: %s", exc, exc_info=exc)
        log.error("operation failed: %r", exc)
        log.warning("failure %(error)s", {"error": exc})
        log.error(exc)
    finally:
        log.removeHandler(handler)
    return stream.getvalue()


def renderings(exc: BaseException) -> dict[str, str]:
    """Every text a ``TabrionError`` produces for a reader without access to its attributes."""
    out = {
        "str": str(exc),
        "repr": repr(exc),
        "args": repr(exc.args),
        "notes": repr(getattr(exc, "__notes__", None)),
        "format_exception": _format(exc),
        "stderr": _stderr(exc),
        "log": logged(exc, redact=False),
    }
    if isinstance(exc, TabrionError):
        out["details"] = repr(dict(exc.details))
        out["classification"] = repr(exc.classification)
        out["safe_message"] = safe_message(exc)
        copy = pickle.loads(pickle.dumps(exc))  # noqa: S301 - our own object
        out["pickled"] = f"{copy!s} {copy!r}"
    return out


def _check(found: dict[str, str], canaries: Iterable[str]) -> None:
    hits = [f"{name}: {canary}" for canary in canaries for name, text in found.items() if canary in text]
    assert not hits, "canary leaked in " + "; ".join(hits) + "\n" + "\n".join(f"--- {k}\n{v}" for k, v in found.items())


def assert_clean(exc: BaseException, *canaries: str, context: type[BaseException] | None = None) -> None:
    """No canary in any rendering of ``exc``, an error raised by Tabrion; no ``__cause__``.

    ``context`` names the type of an allowed ``__context__`` (it must be suppressed); without it there is none.
    """
    assert canaries
    assert isinstance(exc, TabrionError), f"not raised by Tabrion: {type(exc).__name__}"
    assert exc.__cause__ is None, f"{exc!r} chains {type(exc.__cause__).__name__} as __cause__"
    if context is None:
        assert exc.__context__ is None, f"{exc!r} has the __context__ {type(exc.__context__).__name__}"
    else:
        assert isinstance(exc.__context__, context) and exc.__suppress_context__
    _check(renderings(exc), canaries)


def assert_redacted(exc: BaseException, *canaries: str, leaks: bool = False) -> None:
    """No canary in what Tabrion makes of a driver error ``exc``: ``safe_message``, ``classify`` and the log filter.

    ``leaks=True`` also asserts that the raw traceback holds every canary (positive control).
    """
    assert canaries
    if leaks:
        raw = _format(exc)
        missing = [c for c in canaries if c not in raw]
        assert not missing, f"positive control: the raw error does not carry {missing}:\n{raw}"
    found = {
        "safe_message": safe_message(exc),
        "classify": repr(classify(exc)),
        "log": logged(exc, redact=True),
    }
    try:
        raise RuntimeError("wrapped by the caller") from exc
    except RuntimeError as wrapper:
        found["log of a wrapper"] = logged(wrapper, redact=True)
    _check(found, canaries)


def assert_logs_clean(records: Iterable[logging.LogRecord], *canaries: str) -> None:
    """No canary in captured records (``caplog.records``): message, arguments and the formatted traceback."""
    formatter = logging.Formatter()
    texts = {}
    for i, record in enumerate(records):
        text = f"{record.name} {record.levelname} {record.getMessage()} {record.args!r}"
        if record.exc_info:
            text += formatter.formatException(record.exc_info)
        texts[f"record {i}"] = text
    _check(texts, canaries)


def module_fully_selected(request: Any) -> bool:
    """Whether the session runs every test of the requesting test's module (no ``-k`` or ``-m`` left one out)."""
    module = request.node.parent
    wanted: set[str] = set()
    stack = list(module.collect())
    while stack:
        node = stack.pop()
        if hasattr(node, "collect") and not hasattr(node, "runtest"):
            stack.extend(node.collect())
        else:
            wanted.add(node.nodeid)
    selected = {item.nodeid for item in request.session.items}
    return wanted <= selected
