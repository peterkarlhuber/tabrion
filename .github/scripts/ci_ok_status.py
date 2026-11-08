#!/usr/bin/env python3
"""Post ci-ok's verdict as the commit status `ci-ok` on the pull request's head SHA (G-T1 condition C-7, F-06).

ci_ok.py (byte-identical to the canonical copy in nodestro-studio) evaluates the mandatory checks and writes its
verdicts with --json. A workflow_run job's own check run is attached to the default branch's commit, so it can never be
a required pull request check. This script turns the verdict into a commit status on `workflow_run.head_sha`, which
branch protection can require:

  OK       -> success     every mandatory check ran on the head SHA and passed
  PENDING  -> pending     listed workflows are still running; a later completion re-evaluates
  NOT OK   -> failure     a mandatory check failed, was cancelled, is missing or an impostor
  no report or an evaluation error -> error

It fails closed: several targets on one head SHA (several pull requests) give success only when all are OK. Standard
library only; it never checks out or runs pull request code. Usage (in ci-ok.yml):

  python3 -I default/.github/scripts/ci_ok_status.py --report report.json

Environment: GH_TOKEN (needs `statuses: write`), GITHUB_REPOSITORY, GITHUB_EVENT_PATH, GITHUB_SERVER_URL, GITHUB_RUN_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

CONTEXT = "ci-ok"
STATES = {"OK": "success", "PENDING": "pending", "NOT OK": "failure"}
DESCRIPTION_MAX = 140  # GitHub truncates longer descriptions


def combined_state(report: list[dict] | None) -> tuple[str, str]:
    """(GitHub status state, description) for the verdicts of one ci_ok.py run."""
    if not report:
        return "error", "ci-ok produced no verdict for this head SHA"
    states = {STATES.get(entry.get("verdict", ""), "error") for entry in report}
    # The worst state wins: an error or a failure on any target is never hidden by an OK on another.
    state = next((s for s in ("error", "failure", "pending") if s in states), "success")
    verdicts = ", ".join(f"{entry.get('target', '?')}: {entry.get('verdict', '?')}" for entry in report)
    return state, verdicts[:DESCRIPTION_MAX]


def load_report(path: str) -> list[dict] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def head_sha(event_path: str | None) -> str | None:
    """The PR head SHA of a pull_request workflow_run, else None (nothing to post)."""
    if not event_path or not os.path.exists(event_path):
        return None
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)
    run = event.get("workflow_run") or {}
    if run.get("event") != "pull_request":
        return None
    sha = run.get("head_sha")
    return sha if isinstance(sha, str) and len(sha) == 40 else None


def post_status(repo: str, sha: str, token: str, body: dict, api: str = "https://api.github.com") -> int:
    request = urllib.request.Request(
        f"{api}/repos/{repo}/statuses/{sha}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-ok-status",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.status)


def main(argv: list[str] | None = None, env: dict | None = None, poster=post_status) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", required=True, help="the --json file of ci_ok.py")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    sha = head_sha(env.get("GITHUB_EVENT_PATH"))
    if sha is None:
        print("::notice title=ci-ok status::not a pull_request workflow_run: no status posted")
        return 0
    state, description = combined_state(load_report(args.report))
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repo = env.get("GITHUB_REPOSITORY", "")
    body = {
        "state": state,
        "context": CONTEXT,
        "description": description,
        "target_url": f"{server}/{repo}/actions/runs/{env.get('GITHUB_RUN_ID', '')}",
    }
    status = poster(repo, sha, env.get("GH_TOKEN", ""), body)
    print(f"ci-ok status {state!r} posted on {sha[:12]} (HTTP {status}): {description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
