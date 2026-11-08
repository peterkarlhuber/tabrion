"""Tests for ci_ok_status.py (G-T1 condition C-7); standard library only.

Run: python3 -I -m unittest discover -s .github/scripts -p "test_ci_ok*.py"
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_ok_status as cs

SHA = "a" * 40


class CombinedStateTest(unittest.TestCase):
    def test_ok(self) -> None:
        self.assertEqual(cs.combined_state([{"target": "PR #2", "verdict": "OK"}])[0], "success")

    def test_pending(self) -> None:
        self.assertEqual(cs.combined_state([{"verdict": "PENDING"}])[0], "pending")

    def test_not_ok(self) -> None:
        self.assertEqual(cs.combined_state([{"verdict": "NOT OK"}])[0], "failure")

    def test_no_report_is_an_error(self) -> None:
        self.assertEqual(cs.combined_state(None)[0], "error")
        self.assertEqual(cs.combined_state([])[0], "error")

    def test_unknown_verdict_is_an_error(self) -> None:
        self.assertEqual(cs.combined_state([{"verdict": "MAYBE"}])[0], "error")

    def test_worst_target_wins(self) -> None:
        report = [{"verdict": "OK"}, {"verdict": "PENDING"}, {"verdict": "NOT OK"}]
        self.assertEqual(cs.combined_state(report)[0], "failure")
        self.assertEqual(cs.combined_state([{"verdict": "OK"}, {"verdict": "PENDING"}])[0], "pending")

    def test_description_is_short(self) -> None:
        report = [{"target": "PR #1 @ " + "b" * 200, "verdict": "OK"}]
        self.assertLessEqual(len(cs.combined_state(report)[1]), cs.DESCRIPTION_MAX)


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.posted: list[tuple[str, str, dict]] = []

    def _poster(self, repo: str, sha: str, token: str, body: dict) -> int:
        self.posted.append((repo, sha, body))
        return 201

    def _file(self, name: str, data: object) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def _env(self, event: dict) -> dict:
        return {
            "GITHUB_EVENT_PATH": self._file("event.json", event),
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "7",
            "GH_TOKEN": "t",
        }

    def test_posts_on_the_head_sha(self) -> None:
        env = self._env({"workflow_run": {"event": "pull_request", "head_sha": SHA}})
        report = self._file("r.json", [{"target": "PR #2", "verdict": "OK"}])
        self.assertEqual(cs.main(["--report", report], env, self._poster), 0)
        ((repo, sha, body),) = self.posted
        self.assertEqual((repo, sha), ("owner/repo", SHA))
        self.assertEqual((body["state"], body["context"]), ("success", "ci-ok"))
        self.assertEqual(body["target_url"], "https://github.com/owner/repo/actions/runs/7")

    def test_missing_report_posts_an_error(self) -> None:
        env = self._env({"workflow_run": {"event": "pull_request", "head_sha": SHA}})
        cs.main(["--report", os.path.join(self.tmp.name, "absent.json")], env, self._poster)
        self.assertEqual(self.posted[0][2]["state"], "error")

    def test_push_runs_post_nothing(self) -> None:
        env = self._env({"workflow_run": {"event": "push", "head_sha": SHA}})
        report = self._file("r.json", [{"verdict": "OK"}])
        self.assertEqual(cs.main(["--report", report], env, self._poster), 0)
        self.assertEqual(self.posted, [])

    def test_malformed_sha_posts_nothing(self) -> None:
        env = self._env({"workflow_run": {"event": "pull_request", "head_sha": "../x"}})
        cs.main(["--report", self._file("r.json", [{"verdict": "OK"}])], env, self._poster)
        self.assertEqual(self.posted, [])


class WorkflowTest(unittest.TestCase):
    """ci-ok.yml posts the status with the least permissions and keeps the evaluation on the default branch."""

    def setUp(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "workflows", "ci-ok.yml"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_permissions_and_steps(self) -> None:
        self.assertIn("statuses: write", self.text)
        self.assertIn('CI_OK_ENFORCE: "1"', self.text)
        self.assertIn("ci_ok.py --root default --json", self.text)
        self.assertIn("default/.github/scripts/ci_ok_status.py", self.text)
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("pull-requests: write", self.text)

    def test_no_other_workflow_may_post_statuses(self) -> None:
        workflows = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workflows")
        for name in sorted(os.listdir(workflows)):
            if name == "ci-ok.yml":
                continue
            with open(os.path.join(workflows, name), encoding="utf-8") as fh:
                self.assertNotIn("statuses: write", fh.read(), name)


if __name__ == "__main__":
    unittest.main()
