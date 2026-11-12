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


PR_EVENT = {"workflow_run": {"event": "pull_request", "head_sha": SHA, "pull_requests": [{"number": 2}]}}


def fake_api(files: list[str], labels: tuple[str, ...] | list[str] = ()) -> cs.Getter:
    def get(path: str) -> object:
        if path.startswith("/repos/owner/repo/pulls/2/files"):
            return [{"filename": f} for f in files] if path.endswith("page=1") else []
        if path == "/repos/owner/repo/pulls/2":
            return {"labels": [{"name": name} for name in labels]}
        raise AssertionError(path)

    return get


class WorkflowGateTest(unittest.TestCase):
    def test_no_gated_change_passes(self) -> None:
        self.assertIsNone(cs.workflow_gate("owner/repo", [2], fake_api(["src/tabrion/sql.py"])))

    def test_workflow_change_without_label_fails(self) -> None:
        for path in (
            ".github/workflows/ci.yml",
            ".github/workflows/new.yml",
            ".github/scripts/ci_ok.py",
            ".github/ci-manifest.json",
        ):
            gate = cs.workflow_gate("owner/repo", [2], fake_api(["README.md", path]))
            self.assertIsNotNone(gate)
            assert gate is not None
            self.assertEqual(gate[0], "failure")
            self.assertIn(cs.REVIEWED_LABEL, gate[1])

    def test_other_github_files_are_not_gated(self) -> None:
        self.assertIsNone(cs.workflow_gate("owner/repo", [2], fake_api([".github/dependabot.yml"])))

    def test_label_lets_it_pass(self) -> None:
        api = fake_api([".github/workflows/ci.yml"], [cs.REVIEWED_LABEL])
        self.assertIsNone(cs.workflow_gate("owner/repo", [2], api))

    def test_other_labels_do_not_count(self) -> None:
        api = fake_api([".github/workflows/ci.yml"], ["reviewed", "workflow-change"])
        self.assertIsNotNone(cs.workflow_gate("owner/repo", [2], api))

    def test_unknown_pull_request_fails_closed(self) -> None:
        gate = cs.workflow_gate("owner/repo", [], fake_api([]))
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate[0], "failure")


class RetryTest(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        calls: list[int] = []
        slept: list[float] = []

        def post() -> int:
            calls.append(1)
            if len(calls) < 3:
                raise OSError("connection reset")
            return 201

        self.assertEqual(cs.post_with_retry(post, slept.append), 201)
        self.assertEqual(slept, [2, 4])

    def test_never_posted_raises(self) -> None:
        slept: list[float] = []
        with self.assertRaises(RuntimeError):
            cs.post_with_retry(lambda: 500, slept.append)
        self.assertEqual(slept, list(cs.RETRY_DELAYS))


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

    def _main(
        self,
        report: str,
        event: dict,
        files: tuple[str, ...] | list[str] = (),
        labels: tuple[str, ...] | list[str] = (),
    ) -> int:
        return cs.main(
            ["--report", report], self._env(event), self._poster, fake_api(list(files), labels), lambda _: None
        )

    def test_posts_on_the_head_sha(self) -> None:
        report = self._file("r.json", [{"target": "PR #2", "verdict": "OK"}])
        self.assertEqual(self._main(report, PR_EVENT, ["src/tabrion/sql.py"]), 0)
        ((repo, sha, body),) = self.posted
        self.assertEqual((repo, sha), ("owner/repo", SHA))
        self.assertEqual((body["state"], body["context"]), ("success", "ci-ok"))
        self.assertEqual(body["target_url"], "https://github.com/owner/repo/actions/runs/7")

    def test_workflow_change_turns_ok_into_failure(self) -> None:
        report = self._file("r.json", [{"target": "PR #2", "verdict": "OK"}])
        self._main(report, PR_EVENT, [".github/workflows/ci.yml"])
        body = self.posted[0][2]
        self.assertEqual(body["state"], "failure")
        self.assertIn(cs.REVIEWED_LABEL, body["description"])

    def test_reviewed_workflow_change_is_ok(self) -> None:
        report = self._file("r.json", [{"target": "PR #2", "verdict": "OK"}])
        self._main(report, PR_EVENT, [".github/workflows/ci.yml"], [cs.REVIEWED_LABEL])
        self.assertEqual(self.posted[0][2]["state"], "success")

    def test_missing_report_posts_an_error(self) -> None:
        self._main(os.path.join(self.tmp.name, "absent.json"), PR_EVENT)
        self.assertEqual(self.posted[0][2]["state"], "error")

    def test_push_runs_post_nothing(self) -> None:
        report = self._file("r.json", [{"verdict": "OK"}])
        self.assertEqual(self._main(report, {"workflow_run": {"event": "push", "head_sha": SHA}}), 0)
        self.assertEqual(self.posted, [])

    def test_malformed_sha_posts_nothing(self) -> None:
        self._main(
            self._file("r.json", [{"verdict": "OK"}]), {"workflow_run": {"event": "pull_request", "head_sha": "../x"}}
        )
        self.assertEqual(self.posted, [])

    def test_failed_post_fails_the_job(self) -> None:
        report = self._file("r.json", [{"verdict": "OK"}])
        env = self._env(PR_EVENT)
        result = cs.main(["--report", report], env, lambda *a: 503, fake_api([]), lambda _: None)
        self.assertEqual(result, 1)


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
        self.assertIn("pull-requests: read", self.text)

    def test_no_other_workflow_may_post_statuses(self) -> None:
        workflows = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workflows")
        for name in sorted(os.listdir(workflows)):
            if name == "ci-ok.yml":
                continue
            with open(os.path.join(workflows, name), encoding="utf-8") as fh:
                self.assertNotIn("statuses: write", fh.read(), name)


if __name__ == "__main__":
    unittest.main()
