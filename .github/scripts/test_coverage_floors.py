"""Tests for coverage_floors.py (K1); standard library only.

Run: python3 -I -m unittest discover -s .github/scripts -p "test_*.py"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coverage_floors as cf


def report(total: float = 94.05, errors: float = 95.0, branches: int = 10) -> dict[str, object]:
    return {
        "totals": {"percent_covered": total, "num_branches": branches},
        "files": {
            "src/tabrion/errors.py": {"summary": {"percent_covered": errors}},
            "src/tabrion/sql.py": {"summary": {"percent_covered": 100.0}},
        },
    }


class CheckTest(unittest.TestCase):
    def test_all_floors_hold(self) -> None:
        self.assertEqual(cf.check(report(), 94, [("src/tabrion/errors.py", 95), ("src/tabrion/sql.py", 95)]), [])

    def test_total_below(self) -> None:
        self.assertEqual(len(cf.check(report(total=93.99), 94, [])), 1)

    def test_module_below(self) -> None:
        problems = cf.check(report(errors=94.99), None, [("src/tabrion/errors.py", 95)])
        self.assertEqual(len(problems), 1)
        self.assertIn("errors.py", problems[0])

    def test_missing_module_fails(self) -> None:
        self.assertIn("not in the report", cf.check(report(), None, [("src/tabrion/gone.py", 1)])[0])

    def test_no_branch_data_fails(self) -> None:
        self.assertIn("no branch data", cf.check(report(branches=0), None, [])[0])

    def test_not_a_report(self) -> None:
        self.assertEqual(len(cf.check({}, 1, [])), 1)


class ParseTest(unittest.TestCase):
    def test_floor(self) -> None:
        self.assertEqual(cf.parse_floor("src/a.py=94.9"), ("src/a.py", 94.9))
        for bad in ("src/a.py", "=95", "src/a.py=x", "src/a.py=101"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                cf.parse_floor(bad)


class MainTest(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "coverage.json")
            with open(path, "w") as f:
                json.dump(report(), f)
            self.assertEqual(cf.main(["--report", path, "--total", "94", "--floor", "src/tabrion/errors.py=95"]), 0)
            self.assertEqual(cf.main(["--report", path, "--total", "95"]), 1)
            self.assertEqual(cf.main(["--report", path, "--floor", "src/tabrion/missing.py=1"]), 1)


if __name__ == "__main__":
    unittest.main()
