#!/usr/bin/env python3
"""Record the consumer golden files ``tests/golden/<consumer>.json`` from the consumers' own code (TAB-022a).

For every consumer of contract section 8 this script clones the repository at the pinned commit (``CONSUMERS``),
installs it into a throwaway virtual environment of its own (the consumers pin conflicting versions of each other,
and Tabrion must not be importable there), and runs its recorder ``tests/golden/consumers/record_<name>.py`` with
that environment's Python. The recorder runs the shared scenarios of ``tests/golden/consumers/scenarios.py`` with the
consumer's code; the file it writes names the repository and the full commit SHA, and the Python, SQLite and
PostgreSQL versions it was recorded with. The tests replay the same scenarios with Tabrion and compare.

Recording needs network access (``git clone``, ``pip install``) and a PostgreSQL server: ``--admin-url`` (default
``TABRION_TEST_POSTGRES``) names a role with CREATEDB and CREATEROLE. Everything the scenarios create there starts
with a fresh prefix ``t_<hex6>_rec_`` and is dropped again by exact name; nothing else on the server is touched.

To move a consumer to a new commit, change its SHA in ``CONSUMERS``, run the script, and review the diff of the
golden file: every change is a change in the consumer's behavior that the contract table (section 8) must reflect.

Usage: ``python scripts/record_golden.py [--consumer NAME ...] [--admin-url URL] [--keep]``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"


@dataclass(frozen=True)
class Consumer:
    name: str
    repository: str
    commit: str
    extras: str = ""
    """The extras to install (``"postgres"``), comma-separated."""
    contract: str = ""
    """The section of the contract the records belong to."""


CONSUMERS = (
    Consumer(
        "axentra",
        "https://github.com/peterkarlhuber/axentra",
        "ea7aa9f8e0af493f1ce70fc06e49cc975cf8795a",
        contract="8.1",
    ),
    Consumer(
        "nodestro",
        "https://github.com/peterkarlhuber/nodestro-runtime",
        "463e0d99ca66cfdf61f9581e271fc5e0361e0970",
        extras="postgres",
        contract="8.2",
    ),
    Consumer(
        "nodestro_api",
        "https://github.com/peterkarlhuber/nodestro-api",
        "221f9e16025fff12336cad74f99627fdc5ba55cd",
        contract="8.2",
    ),
    Consumer(
        "orkexa",
        "https://github.com/peterkarlhuber/orkexa",
        "5bf139e41893a4c1a757ed53351466e43da7fec0",
        extras="postgres",
        contract="8.3",
    ),
)


def _run(*command: str | Path, env: dict[str, str] | None = None, capture: bool = False) -> str:
    result = subprocess.run(  # noqa: S603 - fixed commands of this script
        [str(part) for part in command],
        check=True,
        env=env,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def record(consumer: Consumer, admin_url: str, work: Path) -> Path:
    """Clone, install and run the recorder of ``consumer``; write and return its golden file."""
    source = work / consumer.name / "source"
    venv = work / consumer.name / "venv"
    _run("git", "init", "--quiet", source)
    _run("git", "-C", source, "fetch", "--quiet", "--depth", "1", consumer.repository, consumer.commit)
    _run("git", "-C", source, "-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD")
    head = _run("git", "-C", source, "rev-parse", "HEAD", capture=True).strip()
    if head != consumer.commit:
        raise SystemExit(f"{consumer.name}: fetched {head}, expected {consumer.commit}")
    _run(sys.executable, "-m", "venv", venv)
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    target = f"{source}[{consumer.extras}]" if consumer.extras else str(source)
    _run(python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", target)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "TABRION_GOLDEN_ADMIN_URL": admin_url,
        "TABRION_GOLDEN_PREFIX": f"t_{secrets.token_hex(3)}_rec_",
        "TABRION_GOLDEN_SOURCE": str(source),
    }
    output = json.loads(_run(python, "-m", f"tests.golden.consumers.record_{consumer.name}", env=env, capture=True))
    document = {
        "about": (
            f"Behavior of {consumer.name} at the commit below for contract section {consumer.contract} (TAB-022a), "
            f"recorded by scripts/record_golden.py with tests/golden/consumers/record_{consumer.name}.py. "
            "Do not edit by hand: change the commit in scripts/record_golden.py and record again."
        ),
        "consumer": consumer.name,
        "repository": consumer.repository,
        "commit": consumer.commit,
        "recorded_with": output["environment"],
        "records": output["records"],
    }
    path = GOLDEN / f"{consumer.name}.json"
    path.write_text(json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    names = [c.name for c in CONSUMERS]
    parser.add_argument("--consumer", action="append", choices=names, help="only this consumer (repeatable)")
    parser.add_argument("--admin-url", default=os.environ.get("TABRION_TEST_POSTGRES", ""))
    parser.add_argument("--keep", action="store_true", help="keep the clones and environments (prints where)")
    args = parser.parse_args(argv)
    if not args.admin_url:
        parser.error("--admin-url (or TABRION_TEST_POSTGRES) is required")
    chosen = [c for c in CONSUMERS if not args.consumer or c.name in args.consumer]
    work = Path(tempfile.mkdtemp(prefix="tabrion-record-golden-"))
    try:
        for consumer in chosen:
            print(f"{consumer.name} @ {consumer.commit[:12]}: {record(consumer, args.admin_url, work)}")
    finally:
        if args.keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
