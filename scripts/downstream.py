#!/usr/bin/env python3
"""Run the consumers' database tests against Tabrion HEAD: the early warning of TAB-022b (specification S-7).

For each consumer (Axentra, ``orkexa[postgres]``, ``nodestro-runtime[postgres]``; ``CONSUMERS``) this script clones
its ``master``, installs it with its test extra into a throwaway virtual environment, creates a throwaway database
for it, points the consumer's own test variable at it and runs its database test subset. The result is a Markdown
table, printed and, with ``--summary`` (in CI: ``$GITHUB_STEP_SUMMARY``), appended to that file.

**HEAD replaces the consumer's Tabrion.** Tabrion is not published to an index; a consumer pins it by Git commit
(``tabrion @ git+https://github.com/peterkarlhuber/tabrion@<sha>``), directly or through another package. A pip
constraint cannot replace such a pin (it only narrows it, and HEAD's version or URL then conflicts), so the install
runs through ``uv pip install --override`` with ``tabrion[all] @ file://<this checkout>``: every requirement on
Tabrion, a Git pin, a version range or a transitive one, resolves to HEAD, and nothing is fetched for the pinned
commit. An override replaces a requirement with its extras, so it names ``all``: a consumer asking for
``tabrion[sqlalchemy]`` keeps SQLAlchemy (``HEAD_EXTRAS``). ``--reinstall-package tabrion`` rebuilds the checkout
instead of using a cached build. After the install the
script checks that the environment's ``tabrion`` is this checkout (its ``direct_url.json``) and reports the consumer
as "setup failed" otherwise. ``uv`` is the one of this interpreter (the ``dev`` extra) or ``UV_VERSION``, installed
into the work directory. There is no package edge from Tabrion to a platform (P6).

The job is not blocking (plan document 04, TAB-022b): the exit code is 0 unless ``--strict`` is given, in which
case any failed consumer makes it 1. A red run blocks the next Tabrion release (release gate, S-7); the nightly
workflow job that runs this script is TODO TB-070. Every step has a timeout (``--minutes`` for a consumer's test
run); a clone, install, database or timeout failure is a failed row of that consumer, and the others still run. A
failed ``uv`` install gives every selected consumer a failed row. A throwaway database that cannot be dropped does
not change the consumer's result; the table's ``Note`` column names it.

``--admin-url`` (default ``TABRION_TEST_POSTGRES``) names a role with CREATEDB and CREATEROLE. Every database the
script creates is ``t_<hex6>_ds_<consumer>`` and is dropped again by that exact name. The consumers' own suites
create and keep fixed roles of their own (``axentra_test_app``, ``nodestro_test_runtime``, ``orkexa_test_runtime``):
run this on a throwaway server (CI's service container), not on a shared one.

Usage: ``python scripts/downstream.py [--consumer NAME ...] [--admin-url URL] [--summary FILE] [--minutes N]
[--strict]``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Consumer:
    name: str
    repository: str
    extras: str
    """Installed with the clone: the test extra and the PostgreSQL extra."""
    url_variable: str
    """The consumer's own variable for its test database."""
    require_variable: str | None
    """Its switch that turns a missing server into a failure."""
    tests: tuple[str, ...]
    """The pytest arguments of its database test subset."""


CONSUMERS = (
    Consumer(
        "axentra",
        "https://github.com/peterkarlhuber/axentra",
        "dev",
        "AXENTRA_TEST_DATABASE_URL",
        None,
        ("tests", "-m", "postgres"),
    ),
    Consumer(
        "orkexa",
        "https://github.com/peterkarlhuber/orkexa",
        "postgres,dev",
        "ORKEXA_TEST_POSTGRES",
        "ORKEXA_REQUIRE_POSTGRES",
        ("tests/backends/postgres", "-m", "postgres"),
    ),
    Consumer(
        "nodestro",
        "https://github.com/peterkarlhuber/nodestro-runtime",
        "postgres,dev",
        "NODESTRO_TEST_POSTGRES",
        "NODESTRO_REQUIRE_POSTGRES",
        (
            "tests/test_storage_postgres.py",
            "tests/test_postgres_pool.py",
            "tests/test_pg_partitions.py",
            "tests/test_pg_drift.py",
            "tests/test_pgvector_status.py",
        ),
    ),
)


@dataclass
class Outcome:
    consumer: str
    commit: str
    passed: bool
    detail: str
    seconds: float
    note: str = ""
    """Something that went wrong besides the result, such as a throwaway database that could not be dropped."""


UV_VERSION = "0.11.32"
"""The ``uv`` installed into the work directory when this interpreter has none."""
HEAD_EXTRAS = "all"
"""The extras HEAD is installed with. An override replaces a requirement whole, extras included, so a consumer asking
for ``tabrion[sqlalchemy]`` would lose SQLAlchemy under a narrower one (review N-1 of #19); ``all`` covers every
runtime extra."""
SETUP_SECONDS = 1800
"""The limit of each clone, ``venv`` and install step."""


class SetupFailed(Exception):
    """A step before the consumer's tests failed; the message is the report's detail."""


def _run(*command: str | Path, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed commands of this script
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        timeout=SETUP_SECONDS,
    )
    return result.stdout


def find_uv(work: Path) -> Path:
    """Return a ``uv`` binary: this interpreter's (the ``dev`` extra), else ``UV_VERSION`` in ``work``."""
    try:
        import uv  # optional, from the dev extra

        return Path(uv.find_uv_bin())
    except (ImportError, FileNotFoundError):
        pass
    tools = work / "tools"
    _run(sys.executable, "-m", "venv", tools)
    tool_python = tools / ("Scripts" if os.name == "nt" else "bin") / "python"
    _run(tool_python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", f"uv=={UV_VERSION}")
    return tools / ("Scripts" if os.name == "nt" else "bin") / ("uv.exe" if os.name == "nt" else "uv")


_INSTALLED_URL = (
    "import importlib.metadata as m, json; "
    "d = m.distribution('tabrion').read_text('direct_url.json'); "
    "print(json.loads(d)['url'] if d else '')"
)


def head_requirement(url: str) -> str:
    """The requirement that replaces every Tabrion requirement of a consumer: ``tabrion[HEAD_EXTRAS] @ url``."""
    return f"tabrion[{HEAD_EXTRAS}] @ {url}"


def install_with_head(
    uv: Path,
    python: Path,
    requirements: list[str],
    *,
    head: str,
    extra: tuple[str, ...] = (),
) -> None:
    """Install ``requirements`` into ``python``'s environment with every Tabrion requirement replaced by ``head``.

    ``head`` is a requirement ``tabrion[...] @ <url>``. Raises ``SetupFailed`` when the environment's ``tabrion``
    is not ``<url>`` afterwards.
    """
    overrides = python.parent.parent / "tabrion-override.txt"
    overrides.write_text(head + "\n", encoding="utf-8")
    _run(
        uv,
        "pip",
        "install",
        "--quiet",
        "--python",
        python,
        "--override",
        overrides,
        "--reinstall-package",
        "tabrion",
        *extra,
        *requirements,
        head,
    )
    wanted = head.partition("@")[2].strip()
    installed = _run(python, "-I", "-c", _INSTALLED_URL).strip()
    if installed.rstrip("/") != wanted.rstrip("/"):
        raise SetupFailed("setup failed (tabrion is not HEAD)")


def _database(admin_url: str, name: str, *, drop: bool) -> str:
    import psycopg  # only for a run against a server
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as conn:
            if drop:
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            else:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.Error as exc:  # no message: it may name the host
        step = "drop" if drop else "create"
        raise SetupFailed(f"{step} database failed ({type(exc).__name__}, SQLSTATE {exc.sqlstate or '-'})") from exc
    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    return str(make_conninfo("", **params))


def run_consumer(consumer: Consumer, admin_url: str, work: Path, uv: Path, minutes: float) -> Outcome:
    started = time.monotonic()
    source = work / consumer.name / "source"
    venv = work / consumer.name / "venv"
    _run("git", "clone", "--quiet", "--depth", "1", consumer.repository, source)
    commit = _run("git", "-C", source, "rev-parse", "HEAD").strip()
    _run(sys.executable, "-m", "venv", venv)
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    try:
        install_with_head(uv, python, [f"{source}[{consumer.extras}]"], head=head_requirement(ROOT.as_uri()))
    except SetupFailed as exc:
        return Outcome(consumer.name, commit, False, str(exc), time.monotonic() - started)

    def tests(url: str) -> Outcome:
        env = {**os.environ, consumer.url_variable: url}
        if consumer.require_variable:
            env[consumer.require_variable] = "1"
        try:
            result = subprocess.run(  # noqa: S603 - the consumer's own test command
                [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", *consumer.tests],
                cwd=source,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=minutes * 60,
            )
        except subprocess.TimeoutExpired:
            return Outcome(consumer.name, commit, False, f"timed out after {minutes:g} min", time.monotonic() - started)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        detail = lines[-1].strip("= ") if lines else "no output"
        return Outcome(consumer.name, commit, result.returncode == 0, detail, time.monotonic() - started)

    try:
        return with_database(admin_url, f"t_{secrets.token_hex(3)}_ds_{consumer.name}", tests)
    except SetupFailed as exc:  # the database could not be created
        return Outcome(consumer.name, commit, False, str(exc), time.monotonic() - started)


def with_database(admin_url: str, name: str, body: Callable[[str], Outcome]) -> Outcome:
    """Create the throwaway database ``name``, run ``body`` with its URL and drop it again by that name.

    A failed create raises ``SetupFailed``. A failed drop does not replace ``body``'s result: it goes into the
    outcome's ``note`` (the result stands; the database is left behind and named)."""
    url = _database(admin_url, name, drop=False)
    outcome: Outcome | None = None
    try:
        outcome = body(url)
    finally:
        try:
            _database(admin_url, name, drop=True)
        except SetupFailed as exc:
            if outcome is not None:  # else body raised, and its exception goes on
                outcome.note = f"{exc}; {name} left behind"
    return outcome


def _cell(text: str) -> str:
    """One Markdown table cell: no line break, ``|`` escaped."""
    return " ".join(text.split()).replace("\\", "\\\\").replace("|", "\\|")


def report(outcomes: list[Outcome], head: str) -> str:
    rows = [
        f"## Downstream: consumers against Tabrion {head[:12]}",
        "",
        "| Consumer | Commit | Result | Tests | Minutes | Note |",
        "|---|---|---|---|---|---|",
    ]
    for item in outcomes:
        result = "passed" if item.passed else "**failed**"
        rows.append(
            f"| {_cell(item.consumer)} | `{_cell(item.commit[:12])}` | {result} | {_cell(item.detail)} "
            f"| {item.seconds / 60:.1f} | {_cell(item.note)} |"
        )
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--consumer", action="append", choices=[c.name for c in CONSUMERS])
    parser.add_argument("--admin-url", default=os.environ.get("TABRION_TEST_POSTGRES", ""))
    parser.add_argument("--summary", type=Path, help="append the Markdown report to this file")
    parser.add_argument("--minutes", type=float, default=30.0, help="limit of each consumer's test run")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a consumer fails")
    args = parser.parse_args(argv)
    if not args.admin_url:
        parser.error("--admin-url (or TABRION_TEST_POSTGRES) is required")
    head = _run("git", "-C", ROOT, "rev-parse", "HEAD").strip()
    outcomes: list[Outcome] = []
    work = Path(tempfile.mkdtemp(prefix="tabrion-downstream-"))
    try:
        selected = [c for c in CONSUMERS if not args.consumer or c.name in args.consumer]
        try:
            uv = find_uv(work)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:  # the fallback needs the network
            failure = f"setup failed (installing uv {UV_VERSION}: {type(exc).__name__})"
            outcomes.extend(Outcome(consumer.name, "-", False, failure, 0.0) for consumer in selected)
            selected = []
        for consumer in selected:
            try:
                outcomes.append(run_consumer(consumer, args.admin_url, work, uv, args.minutes))
            except subprocess.CalledProcessError as exc:  # clone or install failed: a failure of this consumer
                outcomes.append(Outcome(consumer.name, "-", False, f"setup failed (exit {exc.returncode})", 0.0))
            except subprocess.TimeoutExpired as exc:
                outcomes.append(Outcome(consumer.name, "-", False, f"setup timed out ({exc.timeout:g} s)", 0.0))
            except SetupFailed as exc:
                outcomes.append(Outcome(consumer.name, "-", False, str(exc), 0.0))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = report(outcomes, head)
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if args.strict and not all(item.passed for item in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
