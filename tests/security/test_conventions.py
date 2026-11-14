"""QA-22: the repository conventions that orkexa and axentra also follow stay in place (conventions audit,
``docs/program/reports/qa-22-conventions.md``). Actions pins and image digests are TAB-T-181
(``test_workflow_pins.py``); this file covers the rest of the checklist that a file can show."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_license_is_proprietary_in_the_file_and_the_metadata() -> None:
    assert _text(ROOT / "LICENSE").startswith("Tabrion\nCopyright (c) 2026 Levuvo. All rights reserved.\n")
    project = tomllib.loads(_text(ROOT / "pyproject.toml"))["project"]
    assert project["license"] == "LicenseRef-Proprietary"
    assert "LICENSE" in project["license-files"]


def test_security_policy_names_a_private_reporting_route() -> None:
    policy = _text(ROOT / "SECURITY.md")
    assert "## Reporting a vulnerability" in policy
    assert "private vulnerability reporting" in policy
    assert "do not report vulnerabilities in public issues" in policy


def test_community_files_are_present() -> None:
    for name in ("README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "CHANGELOG.md"):
        assert (ROOT / name).is_file(), name
    assert (GITHUB / "pull_request_template.md").is_file()


def _dependabot_updates() -> dict[str, str]:
    """``{ecosystem: the text of its block}`` of ``.github/dependabot.yml`` (no YAML parser in the dev extra)."""
    text = _text(GITHUB / "dependabot.yml")
    blocks = re.split(r"^  - (?=package-ecosystem:)", text, flags=re.MULTILINE)[1:]
    return {re.match(r"package-ecosystem: (\S+)", block)[1]: block for block in blocks}  # type: ignore[index]


def test_dependabot_covers_pip_and_actions_weekly_with_a_cooldown() -> None:
    updates = _dependabot_updates()
    assert set(updates) == {"pip", "github-actions"}
    for ecosystem, block in updates.items():
        assert "interval: weekly" in block, ecosystem
        assert "cooldown:" in block, ecosystem
    # No auto-merge: no workflow merges a Dependabot pull request on its own.
    for workflow in (GITHUB / "workflows").glob("*.yml"):
        assert not _enables_auto_merge(_text(workflow)), workflow.name


def _enables_auto_merge(text: str) -> bool:
    """``gh pr merge --auto``, an ``enable-automerge`` action or the GraphQL ``enablePullRequestAutoMerge``.

    Hyphens and underscores are dropped and the case folded first, so every spelling of the action matches
    (review C-1 of #42: the check must not search for a hyphen after dropping them).
    """
    folded = text.replace("_", "").replace("-", "").lower()
    return "--auto" in text or "enableautomerge" in folded or "enablepullrequestautomerge" in folded


@pytest.mark.parametrize(
    "line",
    [
        'run: gh pr merge --auto --merge "$PR_URL"',
        "uses: peter-evans/enable-pull-request-automerge@v3",
        "uses: some/enable-automerge@v1",
        "uses: some/Enable_AutoMerge@v1",
        "mutation { enablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId } }",
    ],
)
def test_auto_merge_check_turns_red(line: str) -> None:
    assert _enables_auto_merge(f"jobs:\n  merge:\n    steps:\n      - {line}\n")


def test_auto_merge_check_ignores_a_comment_that_rules_it_out() -> None:
    assert not _enables_auto_merge("# No auto-merge: a person merges every Dependabot pull request.\n")


def test_ci_manifest_names_existing_workflows() -> None:
    """The ``ci-ok`` gate judges by ``.github/ci-manifest.json``; every workflow it names must exist and run on pull
    requests (``ci_ok.py`` checks the job names at run time)."""
    manifest = json.loads(_text(GITHUB / "ci-manifest.json"))
    named = set(manifest["pull_request"]) | set(manifest.get("pull_request_path_filtered", {}))
    assert named
    for workflow in named:
        path = GITHUB / "workflows" / workflow
        assert path.is_file(), workflow
        assert re.search(r"^  pull_request:", _text(path), flags=re.MULTILINE), workflow
    assert set(manifest.get("path_filters", {})) == set(manifest.get("pull_request_path_filtered", {}))
    assert (GITHUB / "workflows" / "ci-ok.yml").is_file()
