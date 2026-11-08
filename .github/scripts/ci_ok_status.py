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

Workflow-change gate (Studio CI review F-03): the mandatory jobs run from the pull request's own workflow files, so a
pull request that changes `.github/workflows/**` or `.github/scripts/**` could weaken the checks that judge it, or add
a workflow with `statuses: write` that forges this status. Such a pull request is NOT OK (failure) until it carries
the label `workflow-change-reviewed`, which only the owner or an independent reviewer sets after reading the change.
When the pull request cannot be identified (a fork run without a pull request number), the gate fails closed.

It fails closed in general: several targets on one head SHA give success only when all are OK, and a status that
cannot be posted after retries fails the job loudly (F-11). Standard library only; it never checks out or runs pull
request code. Usage (in ci-ok.yml):

  python3 -I default/.github/scripts/ci_ok_status.py --report report.json

Environment: GH_TOKEN (`statuses: write`, `pull-requests: read`), GITHUB_REPOSITORY, GITHUB_EVENT_PATH,
GITHUB_SERVER_URL, GITHUB_RUN_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

CONTEXT = "ci-ok"
STATES = {"OK": "success", "PENDING": "pending", "NOT OK": "failure"}
SEVERITY = ("error", "failure", "pending", "success")  # worst first
DESCRIPTION_MAX = 140  # GitHub truncates longer descriptions
REVIEWED_LABEL = "workflow-change-reviewed"
GATED_PREFIXES = (".github/workflows/", ".github/scripts/")
RETRY_DELAYS = (2, 4, 8, 16)
API = "https://api.github.com"

Getter = Callable[[str], object]


def combined_state(report: list[dict] | None) -> tuple[str, str]:
    """(GitHub status state, description) for the verdicts of one ci_ok.py run."""
    if not report:
        return "error", "ci-ok produced no verdict for this head SHA"
    states = {STATES.get(entry.get("verdict", ""), "error") for entry in report}
    # The worst state wins: an error or a failure on any target is never hidden by an OK on another.
    state = next((s for s in SEVERITY if s in states), "success")
    verdicts = ", ".join(f"{entry.get('target', '?')}: {entry.get('verdict', '?')}" for entry in report)
    return state, verdicts[:DESCRIPTION_MAX]


def load_report(path: str) -> list[dict] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def load_event(event_path: str | None) -> dict:
    if not event_path or not os.path.exists(event_path):
        return {}
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)
    return event if isinstance(event, dict) else {}


def head_sha(event: dict) -> str | None:
    """The PR head SHA of a pull_request workflow_run, else None (nothing to post)."""
    run = event.get("workflow_run") or {}
    if run.get("event") != "pull_request":
        return None
    sha = run.get("head_sha")
    return sha if isinstance(sha, str) and len(sha) == 40 else None


def pr_numbers(event: dict) -> list[int]:
    run = event.get("workflow_run") or {}
    return [int(p["number"]) for p in run.get("pull_requests") or [] if isinstance(p.get("number"), int)]


def workflow_gate(repo: str, numbers: list[int], get: Getter) -> tuple[str, str] | None:
    """None when the gate passes; else (state, description).

    A pull request that changes a gated path needs the label REVIEWED_LABEL; without a known pull request the gate
    fails closed.
    """
    if not numbers:
        return "failure", "pull request unknown: the workflow-change check fails closed"
    for number in numbers:
        changed: list[str] = []
        for page in range(1, 31):  # the API lists at most 3000 files
            files = get(f"/repos/{repo}/pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(files, list) or not files:
                break
            changed += [str(f.get("filename", "")) for f in files] + [
                str(f["previous_filename"]) for f in files if f.get("previous_filename")
            ]
            if len(files) < 100:
                break
        gated = sorted(p for p in changed if p.startswith(GATED_PREFIXES))
        if not gated:
            continue
        pr = get(f"/repos/{repo}/pulls/{number}")
        labels = {str(label.get("name")) for label in (pr.get("labels") or [])} if isinstance(pr, dict) else set()
        if REVIEWED_LABEL not in labels:
            return (
                "failure",
                f"PR #{number} changes {len(gated)} workflow/script file(s) ({gated[0]}): needs label {REVIEWED_LABEL}",
            )
    return None


def worst(a: tuple[str, str], b: tuple[str, str] | None) -> tuple[str, str]:
    if b is None:
        return a
    return b if SEVERITY.index(b[0]) <= SEVERITY.index(a[0]) else a


def _request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, object]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-ok-status",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return int(response.status), json.loads(raw) if raw else None


def api_getter(token: str) -> Getter:
    def get(path: str) -> object:
        return _request("GET", f"{API}{path}", token)[1]

    return get


def post_status(repo: str, sha: str, token: str, body: dict) -> int:
    return _request("POST", f"{API}/repos/{repo}/statuses/{sha}", token, body)[0]


def post_with_retry(post: Callable[[], int], sleep: Callable[[float], None] = time.sleep) -> int:
    """Post, retrying network and server errors with backoff; raise when every attempt failed (F-11)."""
    last: Exception | None = None
    for delay in (*RETRY_DELAYS, None):
        try:
            status = post()
            if 200 <= status < 300:
                return status
            last = RuntimeError(f"HTTP {status}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
        if delay is not None:
            print(f"::warning title=ci-ok status::posting failed ({last}); retrying in {delay} s")
            sleep(delay)
    raise RuntimeError(f"could not post the ci-ok status after {len(RETRY_DELAYS) + 1} attempts: {last}")


def main(
    argv: list[str] | None = None,
    env: dict | None = None,
    poster: Callable[[str, str, str, dict], int] = post_status,
    getter: Getter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", required=True, help="the --json file of ci_ok.py")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    event = load_event(env.get("GITHUB_EVENT_PATH"))
    sha = head_sha(event)
    if sha is None:
        print("::notice title=ci-ok status::not a pull_request workflow_run: no status posted")
        return 0
    repo = env.get("GITHUB_REPOSITORY", "")
    token = env.get("GH_TOKEN", "")
    get = getter or api_getter(token)
    state = combined_state(load_report(args.report))
    try:
        gate = workflow_gate(repo, pr_numbers(event), get)
    except Exception as exc:  # an API failure must not pass the gate
        gate = ("error", f"workflow-change check failed: {exc}"[:DESCRIPTION_MAX])
    if gate is not None:
        print(f"::warning title=ci-ok workflow change::{gate[1]}")
    state_name, description = worst(state, gate)
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    body = {
        "state": state_name,
        "context": CONTEXT,
        "description": description[:DESCRIPTION_MAX],
        "target_url": f"{server}/{repo}/actions/runs/{env.get('GITHUB_RUN_ID', '')}",
    }
    try:
        status = post_with_retry(lambda: poster(repo, sha, token, body), sleep)
    except RuntimeError as exc:
        print(f"::error title=ci-ok status::{exc}")
        return 1
    print(f"ci-ok status {state_name!r} posted on {sha[:12]} (HTTP {status}): {description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
