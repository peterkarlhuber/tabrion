"""Helpers of the concurrency and stress suite (QA-4; plan 07 K6, DI-02, DI-03). Standard library only.

``TABRION_STRESS_REPEAT`` sets how often each stress test repeats its scenario: 5 by default (pull requests, K6
"short, 5 repetitions"), 200 in the nightly ``stress`` job. Every wait is bounded: threads and processes are joined
with a timeout, and :func:`hang_guard` ends the whole run with every thread's traceback if a test still hangs (a
deadlock in the code under test must turn CI red, not run into the job timeout).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import faulthandler
import os
import sys
import threading
import time
from typing import Any

DEFAULT_REPEAT = 5
NIGHTLY_REPEAT = 200


def repeat() -> int:
    """Repetitions from ``TABRION_STRESS_REPEAT`` (a positive integer), else :data:`DEFAULT_REPEAT`."""
    value = os.environ.get("TABRION_STRESS_REPEAT", "")
    if not value:
        return DEFAULT_REPEAT
    count = int(value)
    if count < 1:
        raise ValueError("TABRION_STRESS_REPEAT must be a positive integer")
    return count


def _stderr(config: Any) -> Any:
    """The stderr that pytest's faulthandler plugin saved before output capturing (else ``sys.__stderr__``)."""
    try:
        from _pytest.faulthandler import fault_handler_stderr_fd_key

        saved = config.stash.get(fault_handler_stderr_fd_key, None)
    except Exception:  # another pytest version: the dump may then land in the captured output only
        saved = None
    return saved if saved is not None else sys.__stderr__


@contextlib.contextmanager
def hang_guard(config: Any, seconds: float) -> Iterator[None]:
    """After ``seconds``, dump every thread's traceback to stderr and end the process (exit code 1).

    ``config`` is pytest's config (``request.config``): its saved stderr survives output capturing."""
    faulthandler.dump_traceback_later(seconds, exit=True, file=_stderr(config))
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def run_threads(targets: list[Callable[[], None]], *, timeout: float) -> list[BaseException]:
    """Start one thread per target behind a barrier, join them within ``timeout`` in all; return their exceptions.

    A thread still alive after ``timeout`` is reported as a ``TimeoutError`` (it is a daemon, so it cannot keep the
    run alive)."""
    barrier = threading.Barrier(len(targets))
    errors: list[BaseException] = []
    lock = threading.Lock()

    def wrap(target: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                barrier.wait(timeout)
                target()
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        return run

    threads = [threading.Thread(target=wrap(t), daemon=True) for t in targets]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in threads if t.is_alive()]
    if alive:
        errors.append(TimeoutError(f"threads still running after {timeout} s: {alive}"))
    return errors
