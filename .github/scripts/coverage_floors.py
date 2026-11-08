#!/usr/bin/env python3
"""Per-module coverage floors for the CI job "coverage" (planning document 07, K1).

coverage.py has one ``fail_under`` for the total only. This script reads the JSON report (``coverage json`` or
``pytest --cov-report=json:FILE``) and fails when the total or a named file is below its floor. The floors are
arguments in ``.github/workflows/ci.yml``, so lowering one is a workflow change that needs the label
``workflow-change-reviewed`` (ADR 0012). A named file that is missing from the report fails too, so a rename cannot
drop its floor silently. Standard library only. Usage:

  python3 -I .github/scripts/coverage_floors.py --report coverage.json --total 94 \\
      --floor src/tabrion/errors.py=95 --floor src/tabrion/sqlite.py=94.9

The percentages are coverage.py's ``percent_covered`` (statements and branches together when branch coverage is on).
"""

from __future__ import annotations

import argparse
import json
import sys


def parse_floor(text: str) -> tuple[str, float]:
    path, sep, value = text.rpartition("=")
    if not sep or not path:
        raise argparse.ArgumentTypeError(f"expected PATH=PERCENT, got {text!r}")
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a percentage: {value!r}") from None
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError(f"not a percentage: {value!r}")
    return path, number


def check(report: dict[str, object], total: float | None, floors: list[tuple[str, float]]) -> list[str]:
    """Return one line per floor that is not met; an empty list means every floor holds."""
    problems: list[str] = []
    totals = report.get("totals")
    files = report.get("files")
    if not isinstance(totals, dict) or not isinstance(files, dict):
        return ["the report has no 'totals' or 'files' (not a coverage.py JSON report)"]
    if not totals.get("num_branches"):
        problems.append("the report has no branch data; run with branch coverage on")
    if total is not None:
        value = float(totals["percent_covered"])
        if value < total:
            problems.append(f"total: {value:.2f} % is below the floor {total:g} %")
    for path, floor in floors:
        entry = files.get(path)
        if not isinstance(entry, dict):
            problems.append(f"{path}: not in the report (renamed or not measured); update its floor")
            continue
        value = float(entry["summary"]["percent_covered"])
        if value < floor:
            problems.append(f"{path}: {value:.2f} % is below the floor {floor:g} %")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--report", required=True, help="coverage.py JSON report")
    parser.add_argument("--total", type=float, help="floor of the total, in percent")
    parser.add_argument("--floor", type=parse_floor, action="append", default=[], help="PATH=PERCENT, repeatable")
    args = parser.parse_args(argv)
    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    problems = check(report, args.total, args.floor)
    rows = [("total", float(report.get("totals", {}).get("percent_covered", 0.0)), args.total)]
    for path, floor in args.floor:
        entry = report.get("files", {}).get(path)
        rows.append((path, float(entry["summary"]["percent_covered"]) if entry else float("nan"), floor))
    for name, value, floor in rows:
        print(f"{name}: {value:.2f} % (floor {'-' if floor is None else f'{floor:g} %'})")
    for line in problems:
        print(f"::error::coverage floor: {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
