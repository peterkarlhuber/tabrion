#!/usr/bin/env python3
"""Resolve and check the commit that release.yml builds (QA-11, ADR 0009).

Tabrion is consumed through Git SHA pins, and the agent's Git proxy refuses tag pushes, so a release run names its
commit either by an existing annotated tag or by a full 40-hex commit SHA. Either way the commit must be:

* on ``origin/master`` (an ancestor of it, normally the merge commit of the version-bump pull request);
* at a final version: ``__version__`` in ``src/tabrion/_version.py`` is ``X.Y.Z`` (no ``a``, ``b``, ``rc``, ``.dev``
  or ``.post`` part);
* described in ``CHANGELOG.md`` by a dated section ``## [X.Y.Z] - YYYY-MM-DD``;
* for a tag: annotated, and named ``vX.Y.Z`` after that version.

Everything is read from the commit with ``git show``; the working tree is not used. Standard library only; it never
runs code of the commit. Usage (in release.yml, after a full-history checkout of master):

  python3 -I .github/scripts/release_check.py --tag v0.1.0
  python3 -I .github/scripts/release_check.py --sha <40 hex digits>

On success it prints the commit, the version and the label (the tag, or ``v<version>``) and appends ``sha=``,
``version=`` and ``label=`` to ``$GITHUB_OUTPUT`` when that is set. On failure it prints an ``::error::`` line and
exits 1.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys

SHA_RE = re.compile(r"[0-9a-f]{40}")
FINAL_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
MASTER = "origin/master"


class ReleaseError(Exception):
    """A check failed; the message is the ``::error::`` text."""


def check_inputs(tag: str, sha: str) -> None:
    """Exactly one of ``tag`` and ``sha``; a SHA is the full lower-case 40-hex form (no abbreviation, no ref name)."""
    if bool(tag) == bool(sha):
        raise ReleaseError("give exactly one of the inputs 'tag' and 'sha'")
    if sha and not SHA_RE.fullmatch(sha):
        raise ReleaseError(f"sha {sha!r} is not a full 40-digit lower-case commit SHA")
    if tag and not re.fullmatch(r"v[0-9A-Za-z.+-]+", tag):
        raise ReleaseError(f"tag {tag!r} is not a v<version> tag name")


def parse_version(version_file: str) -> str:
    match = VERSION_RE.search(version_file)
    if not match:
        raise ReleaseError("src/tabrion/_version.py has no __version__ line")
    return match.group(1)


def check_final(version: str) -> None:
    if not FINAL_RE.fullmatch(version):
        raise ReleaseError(f"__version__ {version!r} is not a final version X.Y.Z")


def check_changelog(changelog: str, version: str) -> None:
    """A section ``## [<version>] - <ISO date>`` exists; the date is a real calendar date."""
    pattern = re.compile(rf"^## \[{re.escape(version)}\][ \t]*(.*?)[ \t]*$", re.MULTILINE)
    match = pattern.search(changelog)
    if not match:
        raise ReleaseError(f"CHANGELOG.md has no section '## [{version}] - YYYY-MM-DD'")
    rest = match.group(1)
    date = re.fullmatch(r"-[ \t]+([0-9]{4}-[0-9]{2}-[0-9]{2})", rest)
    if not date:
        raise ReleaseError(f"the CHANGELOG.md section [{version}] has no date ('## [{version}] - YYYY-MM-DD')")
    try:
        datetime.date.fromisoformat(date.group(1))
    except ValueError:
        raise ReleaseError(f"the CHANGELOG.md section [{version}] has an invalid date {date.group(1)!r}") from None


def git(root: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)


def show(root: str, commit: str, path: str) -> str:
    result = git(root, "show", f"{commit}:{path}")
    if result.returncode != 0:
        raise ReleaseError(f"{path} does not exist at {commit}")
    return result.stdout


def resolve(root: str, tag: str, sha: str) -> tuple[str, str, str]:
    """Return (commit, version, label) or raise ReleaseError."""
    check_inputs(tag, sha)
    if tag:
        kind = git(root, "cat-file", "-t", f"refs/tags/{tag}")
        if kind.returncode != 0:
            raise ReleaseError(f"tag {tag} does not exist")
        if kind.stdout.strip() != "tag":
            raise ReleaseError(f"{tag} is not an annotated tag")
        commit = git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    else:
        result = git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        if result.returncode != 0 or result.stdout.strip() != sha:
            raise ReleaseError(f"{sha} is not a commit of this repository")
        commit = sha
    if git(root, "rev-parse", "--verify", "--quiet", MASTER).returncode != 0:
        raise ReleaseError(f"{MASTER} is missing; check out with fetch-depth: 0")
    if git(root, "merge-base", "--is-ancestor", commit, MASTER).returncode != 0:
        raise ReleaseError(f"{commit} is not on master")
    version = parse_version(show(root, commit, "src/tabrion/_version.py"))
    check_final(version)
    if tag and tag != f"v{version}":
        raise ReleaseError(f"tag {tag} does not match version {version}")
    check_changelog(show(root, commit, "CHANGELOG.md"), version)
    return commit, version, tag or f"v{version}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--tag", default="", help="an existing annotated tag v<version>")
    parser.add_argument("--sha", default="", help="a full 40-hex commit SHA on master")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    args = parser.parse_args(argv)
    try:
        commit, version, label = resolve(args.root, args.tag.strip(), args.sha.strip())
    except ReleaseError as exc:
        print(f"::error::{exc}")
        return 1
    print(f"release commit {commit}, version {version}, label {label}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"sha={commit}\nversion={version}\nlabel={label}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
