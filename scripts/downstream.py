#!/usr/bin/env python3
"""Run the consumers' database tests against Tabrion HEAD: the early warning of TAB-022b (specification S-7).

For each consumer (Axentra, ``orkexa[postgres]``, ``nodestro-runtime[postgres]``; ``CONSUMERS``) this script clones
its ``master``, installs it with its test extra into a throwaway virtual environment, installs **this** Tabrion
checkout over it through a pip constraint (so a consumer that pins Tabrion gets HEAD instead; no package edge from
Tabrion to a platform, P6), creates a throwaway database for it, points the consumer's own test variable at it
and runs its database test subset. The result is a Markdown table, printed and, with ``--summary`` (in CI:
``$GITHUB_STEP_SUMMARY``), appended to that file.

The job is not blocking (plan document 04, TAB-022b): the exit code is 0 unless ``--strict`` is given, in which
case any failed consumer makes it 1. A red run blocks the next Tabrion release (release gate, S-7); the nightly
workflow job that runs this script is TODO TB-070.

``--admin-url`` (default ``TABRION_TEST_POSTGRES``) names a role with CREATEDB and CREATEROLE. Every database the
script creates is ``t_<hex6>_ds_<consumer>`` and is dropped again by that exact name. The consumers' own suites
create and keep fixed roles of their own (``axentra_test_app``, ``nodestro_test_runtime``, ``orkexa_test_runtime``):
run this on a throwaway server (CI's service container), not on a shared one.

Usage: ``python scripts/downstream.py [--consumer NAME ...] [--admin-url URL] [--summary FILE] [--strict]``.
"""

from __future__ import annotations

import argparse
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


def _run(*command: str | Path, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed commands of this script
        [str(part) for part in command], cwd=cwd, env=env, check=True, text=True, stdout=subprocess.PIPE
    )
    return result.stdout


def _database(admin_url: str, name: str, *, drop: bool) -> str:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as conn:
        if drop:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        else:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    return str(make_conninfo("", **params))


def run_consumer(consumer: Consumer, admin_url: str, work: Path) -> Outcome:
    started = time.monotonic()
    source = work / consumer.name / "source"
    venv = work / consumer.name / "venv"
    _run("git", "clone", "--quiet", "--depth", "1", consumer.repository, source)
    commit = _run("git", "-C", source, "rev-parse", "HEAD").strip()
    _run(sys.executable, "-m", "venv", venv)
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    constraint = work / consumer.name / "constraints.txt"
    constraint.write_text(f"tabrion @ {ROOT.as_uri()}\n", encoding="utf-8")
    pip_env = {**os.environ, "PIP_CONSTRAINT": str(constraint)}
    pip = (python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check")
    _run(*pip, f"{source}[{consumer.extras}]", env=pip_env)
    _run(*pip, f"{ROOT}[postgres]", env=pip_env)  # HEAD, also when the consumer does not depend on Tabrion yet
    name = f"t_{secrets.token_hex(3)}_ds_{consumer.name}"
    url = _database(admin_url, name, drop=False)
    try:
        env = {**os.environ, consumer.url_variable: url}
        if consumer.require_variable:
            env[consumer.require_variable] = "1"
        result = subprocess.run(  # noqa: S603 - the consumer's own test command
            [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", *consumer.tests],
            cwd=source,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        _database(admin_url, name, drop=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    detail = lines[-1].strip("= ") if lines else "no output"
    return Outcome(consumer.name, commit, result.returncode == 0, detail, time.monotonic() - started)


def report(outcomes: list[Outcome], head: str) -> str:
    rows = [
        f"## Downstream: consumers against Tabrion {head[:12]}",
        "",
        "| Consumer | Commit | Result | Tests | Minutes |",
        "|---|---|---|---|---|",
    ]
    for item in outcomes:
        result = "passed" if item.passed else "**failed**"
        rows.append(f"| {item.consumer} | `{item.commit[:12]}` | {result} | {item.detail} | {item.seconds / 60:.1f} |")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--consumer", action="append", choices=[c.name for c in CONSUMERS])
    parser.add_argument("--admin-url", default=os.environ.get("TABRION_TEST_POSTGRES", ""))
    parser.add_argument("--summary", type=Path, help="append the Markdown report to this file")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a consumer fails")
    args = parser.parse_args(argv)
    if not args.admin_url:
        parser.error("--admin-url (or TABRION_TEST_POSTGRES) is required")
    head = _run("git", "-C", ROOT, "rev-parse", "HEAD").strip()
    outcomes: list[Outcome] = []
    work = Path(tempfile.mkdtemp(prefix="tabrion-downstream-"))
    try:
        for consumer in CONSUMERS:
            if args.consumer and consumer.name not in args.consumer:
                continue
            try:
                outcomes.append(run_consumer(consumer, args.admin_url, work))
            except subprocess.CalledProcessError as exc:  # clone or install failed: a failure of this consumer
                outcomes.append(Outcome(consumer.name, "-", False, f"setup failed (exit {exc.returncode})", 0.0))
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
