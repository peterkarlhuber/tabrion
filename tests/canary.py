"""Canary helpers of the value-leak suite (QA-2, TAB-T-110..113; plan 07 TM-02, SR-05, SR-06). Standard library only.

A canary is a unique value (``tabrion-canary-<uuid>``) that a test puts where a secret or a data value goes: a bind
parameter, a row value in a PostgreSQL ``DETAIL``, a password, a token, a file's content. The helpers render an
exception every way Python and ``logging`` render one and fail when the canary appears in any rendering:

- :func:`assert_clean` for an error **Tabrion raises** (``TabrionError``): ``str``, ``repr``, ``args``, ``details``,
  ``classification``, ``__notes__``, ``traceback.format_exception`` and ``print_exception`` (what reaches stderr for an
  uncaught error), the locals of the traceback's frames (what Sentry, ``rich`` or ``pytest -l`` show; frames of the
  test suite itself are left out, they hold the canary on purpose), a plain ``logging`` handler **without**
  :class:`~tabrion.errors.DbErrorRedactionFilter`, and a pickle round trip. ``__cause__`` is ``None``; a
  ``__context__`` is only allowed when it is suppressed and named by the caller (contract I-39: the caller's own
  error from ``Transaction.connection``).
- :func:`assert_redacted` for a **driver error** that Tabrion classifies but does not raise (layer A passes driver
  errors through): ``safe_message``, ``repr(classify(...))`` and a ``logging`` handler with the filter, also for the
  error wrapped in a caller's exception. ``leaks=True`` is the positive control: the canary must be in the raw
  traceback, so the test proves the value reached the error and Tabrion removed it.
"""

from __future__ import annotations

from collections.abc import Iterable
import contextlib
import io
import logging
from pathlib import Path
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


_TESTS = str(Path(__file__).resolve().parent)


def frame_locals(exc: BaseException, *, own: bool = False, skip: tuple[str, ...] = ()) -> str:
    """The locals of every frame of ``exc``'s traceback, as ``capture_locals=True`` renders them (``repr``).

    Frames of the test suite are left out unless ``own`` is true: the test's own frame holds the canary. ``skip``
    leaves out the frames of these files too."""
    summary = traceback.TracebackException.from_exception(exc, capture_locals=True)
    lines = [
        f"{frame.filename}:{frame.lineno} in {frame.name}: {frame.locals!r}"
        for frame in summary.stack
        if own or not (str(Path(frame.filename).resolve()).startswith(_TESTS) or frame.filename in skip)
    ]
    return "\n".join(lines)


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
        "frame locals": frame_locals(exc),
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


def assert_clean(
    exc: BaseException, *canaries: str, context: type[BaseException] | None = None, paths: tuple[str, ...] = ()
) -> None:
    """No canary in any rendering of ``exc``, an error raised by Tabrion; no ``__cause__``.

    ``context`` names the type of an allowed ``__context__`` (it must be suppressed); without it there is none.
    That caller's error is also the ``value`` local of ``contextlib``'s ``__exit__`` frames, which threw it into
    ``Database.transaction()``: those frames are left out (TB-085 item 2). ``paths`` are canaries in a file name:
    paths are no data values, so they may be frame locals (the path arguments of ``connect``), and are kept out of
    every other rendering.
    """
    assert canaries or paths
    assert isinstance(exc, TabrionError), f"not raised by Tabrion: {type(exc).__name__}"
    assert exc.__cause__ is None, f"{exc!r} chains {type(exc.__cause__).__name__} as __cause__"
    if context is None:
        assert exc.__context__ is None, f"{exc!r} has the __context__ {type(exc.__context__).__name__}"
    else:
        assert isinstance(exc.__context__, context) and exc.__suppress_context__
    found = renderings(exc)
    if context is not None:
        found["frame locals"] = frame_locals(exc, skip=(contextlib.__file__,))
    _check(found, canaries)
    _check({name: text for name, text in found.items() if name != "frame locals"}, paths)


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


def capture_all_logs(caplog: Any) -> None:
    """``caplog`` at DEBUG for the root logger, at INFO for ``sqlalchemy`` (which sets itself to WARNING on import).

    Every record of the test reaches the capture, Tabrion's and a dependency's: an application that turns on
    ``sqlalchemy.engine`` at INFO to see its statements must not log the values (contract I-76). At DEBUG,
    SQLAlchemy also logs the rows it fetched, as ``echo="debug"`` does: that level is the application's choice
    like ``echo`` (SR-06), and TB-085 records it."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="sqlalchemy")


def phase_records(caplog: Any) -> list[logging.LogRecord]:
    """The records of the test's setup, call and teardown so far, without SQLAlchemy's records of SQL text.

    In a fixture's teardown ``caplog.records`` holds the teardown's records only: the other phases are read with
    ``get_records``. ``sqlalchemy.engine`` logs each statement as its message without arguments: that is the
    application's SQL text as written (a canary scenario puts a literal into it), not a parameter. Its records of
    the parameters (``[generated in ...] ...``, with arguments) are kept and checked."""
    records = [*caplog.get_records("setup"), *caplog.get_records("call"), *caplog.records]
    return [r for r in records if not (r.name.startswith("sqlalchemy.engine") and not r.args)]


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
