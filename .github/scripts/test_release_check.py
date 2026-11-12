"""Tests for release_check.py (QA-11); standard library only.

Run: python3 -I -m unittest discover -s .github/scripts -p "test_*.py"
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import release_check as rc

CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-11-20\n\n### Added\n\n- x\n"


class InputTest(unittest.TestCase):
    def test_exactly_one(self) -> None:
        with self.assertRaises(rc.ReleaseError):
            rc.check_inputs("", "")
        with self.assertRaises(rc.ReleaseError):
            rc.check_inputs("v0.1.0", "a" * 40)

    def test_sha_must_be_full_lower_case_hex(self) -> None:
        for bad in ("a" * 39, "A" * 40, "master", "a" * 40 + "\n", "g" * 40):
            with self.subTest(bad=bad), self.assertRaises(rc.ReleaseError):
                rc.check_inputs("", bad)
        rc.check_inputs("", "0123456789abcdef" * 2 + "01234567")

    def test_tag_name(self) -> None:
        rc.check_inputs("v0.1.0", "")
        for bad in ("0.1.0", "v0.1.0;id", "refs/tags/v0.1.0"):
            with self.subTest(bad=bad), self.assertRaises(rc.ReleaseError):
                rc.check_inputs(bad, "")


class VersionTest(unittest.TestCase):
    def test_final(self) -> None:
        rc.check_final("0.1.0")
        rc.check_final("1.20.3")
        for bad in ("0.1.0a2", "0.1.0a2.dev0", "0.1.0rc1", "0.1.1.dev0", "0.1.0.post1", "0.1", "v0.1.0"):
            with self.subTest(bad=bad), self.assertRaises(rc.ReleaseError):
                rc.check_final(bad)

    def test_parse(self) -> None:
        self.assertEqual(rc.parse_version('"""Doc."""\n\n__version__ = "0.1.0"\n'), "0.1.0")
        with self.assertRaises(rc.ReleaseError):
            rc.parse_version("VERSION = '0.1.0'\n")


class ChangelogTest(unittest.TestCase):
    def test_dated_section(self) -> None:
        rc.check_changelog(CHANGELOG, "0.1.0")

    def test_missing_section(self) -> None:
        with self.assertRaises(rc.ReleaseError):
            rc.check_changelog(CHANGELOG, "0.2.0")

    def test_undated_or_bad_date(self) -> None:
        for heading in (
            "## [0.1.0]",
            "## [0.1.0] - internal pre-release, not published",
            "## [0.1.0] - 2026-13-01",
            "## [0.1.0] - 2026-11-20 extra",
        ):
            with self.subTest(heading=heading), self.assertRaises(rc.ReleaseError):
                rc.check_changelog(f"# Changelog\n\n{heading}\n", "0.1.0")

    def test_version_is_not_a_prefix_match(self) -> None:
        with self.assertRaises(rc.ReleaseError):
            rc.check_changelog("## [0.1.0a1] - 2026-11-01\n", "0.1.0")


class GitTest(unittest.TestCase):
    """A throwaway repository with master, a side branch and tags."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.run_git("init", "-q", "-b", "master")
        self.run_git("config", "user.email", "t@example.invalid")
        self.run_git("config", "user.name", "t")
        self.run_git("config", "tag.gpgSign", "false")
        self.run_git("config", "commit.gpgSign", "false")
        self.good = self.commit("0.1.0", CHANGELOG)
        self.run_git("tag", "-a", "v0.1.0", "-m", "v0.1.0")
        self.run_git("tag", "v0.1.0-light")
        self.dev = self.commit("0.1.1.dev0", CHANGELOG)
        self.run_git("update-ref", "refs/remotes/origin/master", "HEAD")
        self.run_git("checkout", "-q", "-b", "side")
        self.side = self.commit("0.2.0", CHANGELOG.replace("0.1.0", "0.2.0"))
        self.run_git("tag", "-a", "v0.2.0", "-m", "v0.2.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", self.root, *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, version: str, changelog: str) -> str:
        os.makedirs(os.path.join(self.root, "src", "tabrion"), exist_ok=True)
        with open(os.path.join(self.root, "src", "tabrion", "_version.py"), "w") as f:
            f.write(f'__version__ = "{version}"\n')
        with open(os.path.join(self.root, "CHANGELOG.md"), "w") as f:
            f.write(changelog)
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", version)
        return self.run_git("rev-parse", "HEAD")

    def test_sha_on_master(self) -> None:
        self.assertEqual(rc.resolve(self.root, "", self.good), (self.good, "0.1.0", "v0.1.0"))

    def test_annotated_tag(self) -> None:
        self.assertEqual(rc.resolve(self.root, "v0.1.0", ""), (self.good, "0.1.0", "v0.1.0"))

    def test_lightweight_tag_refused(self) -> None:
        with self.assertRaisesRegex(rc.ReleaseError, "not an annotated tag"):
            rc.resolve(self.root, "v0.1.0-light", "")

    def test_dev_version_refused(self) -> None:
        with self.assertRaisesRegex(rc.ReleaseError, "not a final version"):
            rc.resolve(self.root, "", self.dev)

    def test_not_on_master(self) -> None:
        with self.assertRaisesRegex(rc.ReleaseError, "not on master"):
            rc.resolve(self.root, "", self.side)
        with self.assertRaisesRegex(rc.ReleaseError, "not on master"):
            rc.resolve(self.root, "v0.2.0", "")

    def test_unknown_sha(self) -> None:
        with self.assertRaisesRegex(rc.ReleaseError, "not a commit"):
            rc.resolve(self.root, "", "0" * 40)

    def test_main_writes_outputs(self) -> None:
        out = os.path.join(self.root, "out.txt")
        old = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = out
        try:
            self.assertEqual(rc.main(["--sha", self.good, "--root", self.root]), 0)
            self.assertEqual(rc.main(["--sha", self.dev, "--root", self.root]), 1)
        finally:
            if old is None:
                del os.environ["GITHUB_OUTPUT"]
            else:
                os.environ["GITHUB_OUTPUT"] = old
        with open(out) as f:
            self.assertEqual(f.read(), f"sha={self.good}\nversion=0.1.0\nlabel=v0.1.0\n")


if __name__ == "__main__":
    unittest.main()
