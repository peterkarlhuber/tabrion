"""Loader of the internal "Levuvo Database Profile" vectors (TAB-050; API contract section 11, ADR 0011).

**Not public** and not in the wheel: only Tabrion's own tests use it (K-35). Other repositories never load or vendor
the vectors. ``load`` checks the required fields, the ID pattern and unique IDs; the full JSON Schema is checked by
``validate_schema`` (dev dependency ``jsonschema``). An error names the file and the vector ID, never the content.

``python -m tests.support.conformance --write`` regenerates ``conformance/MANIFEST.json`` after a deliberate change.
The new digest must then be named in ``CHANGELOG.md`` (``LDP digest <first 12 hex digits>``), or the conformance
tests fail.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
CONFORMANCE = ROOT / "conformance"
VECTORS = CONFORMANCE / "vectors"
SCHEMA = CONFORMANCE / "schema" / "vector-file.schema.json"
MANIFEST = CONFORMANCE / "MANIFEST.json"
SPEC = CONFORMANCE / "SPEC.md"
CHANGELOG = ROOT / "CHANGELOG.md"

FORMAT = "ldp-vectors/1"
AREAS = (
    "sqlite-profile",
    "sqlite-transactions",
    "sqlite-migrate",
    "postgres-session",
    "postgres-roles",
    "postgres-locks",
    "postgres-extensions",
    "errors-classification",
    "errors-redaction",
    "sql-split",
)
LEVELS = ("MUST", "SHOULD", "INFO")
ENGINES = ("sqlite", "postgres", "any")
REQUIRED = ("id", "level", "rule", "engine", "action", "expect")
ID_PATTERN = re.compile(r"LDP-[A-Z]{2,4}-\d{3}")
VERSION_PATTERN = re.compile(r"LDP-\d+\.\d+\.\d+")


class VectorFileError(Exception):
    """A vector file is damaged; the message names the file (and the vector ID), never its content."""


@dataclass(frozen=True, slots=True)
class Vector:
    id: str
    level: str
    rule: str
    engine: str
    action: str
    args: dict[str, Any]
    expect: dict[str, Any]
    setup: tuple[str, ...]
    min_server_version: int | None
    provenance: str | None
    area: str


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise VectorFileError(f"{path.name}: not readable JSON") from None
    if not isinstance(data, dict):
        raise VectorFileError(f"{path.name}: not a JSON object")
    return data


def parse(path: Path, data: dict[str, Any]) -> tuple[Vector, ...]:
    """The vectors of one file, after the loader's own checks (fields, ID pattern, unique IDs)."""
    name = path.name
    if data.get("format") != FORMAT:
        raise VectorFileError(f"{name}: format is not {FORMAT}")
    if not isinstance(data.get("standard_version"), str) or not VERSION_PATTERN.fullmatch(data["standard_version"]):
        raise VectorFileError(f"{name}: standard_version is not LDP-x.y.z")
    area = data.get("area")
    if area not in AREAS or path.stem != area:
        raise VectorFileError(f"{name}: area does not match the file name or is unknown")
    raw = data.get("vectors")
    if not isinstance(raw, list) or not raw:
        raise VectorFileError(f"{name}: no vectors")
    seen: set[str] = set()
    out: list[Vector] = []
    for index, item in enumerate(raw):
        vid = item.get("id") if isinstance(item, dict) else None
        label = vid if isinstance(vid, str) and ID_PATTERN.fullmatch(vid) else f"vector #{index}"
        if not isinstance(item, dict) or any(key not in item for key in REQUIRED):
            raise VectorFileError(f"{name} {label}: a required field is missing")
        if not isinstance(vid, str) or not ID_PATTERN.fullmatch(vid):
            raise VectorFileError(f"{name} {label}: the ID does not match LDP-XX-nnn")
        if vid in seen:
            raise VectorFileError(f"{name} {vid}: duplicate ID")
        seen.add(vid)
        action = item["action"]
        if item["level"] not in LEVELS or item["engine"] not in ENGINES:
            raise VectorFileError(f"{name} {vid}: unknown level or engine")
        if not isinstance(action, dict) or not isinstance(action.get("name"), str):
            raise VectorFileError(f"{name} {vid}: action needs a name")
        if not isinstance(item["expect"], dict) or not isinstance(action.get("args", {}), dict):
            raise VectorFileError(f"{name} {vid}: expect and args must be objects")
        out.append(
            Vector(
                id=vid,
                level=item["level"],
                rule=str(item["rule"]),
                engine=item["engine"],
                action=action["name"],
                args=dict(action.get("args", {})),
                expect=dict(item["expect"]),
                setup=tuple(item.get("setup", ())),
                min_server_version=item.get("min_server_version"),
                provenance=item.get("provenance"),
                area=area,
            )
        )
    return tuple(out)


def load(area: str) -> tuple[Vector, ...]:
    """Every vector of ``area`` (``conformance/vectors/<area>.json``)."""
    if area not in AREAS:
        raise VectorFileError(f"{area}: unknown area")
    path = VECTORS / f"{area}.json"
    return parse(path, _read(path))


def load_all() -> tuple[Vector, ...]:
    vectors = tuple(v for area in AREAS for v in load(area))
    ids = [v.id for v in vectors]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise VectorFileError(f"duplicate IDs across files: {duplicates}")
    return vectors


def validate_schema(path: Path) -> None:
    """The full JSON Schema (draft 2020-12) of API contract section 10.1; needs ``jsonschema``."""
    # Imported by name: the dev dependency ships no type stubs, and this keeps mypy --strict independent of them.
    jsonschema: Any = importlib.import_module("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(_read(path)), key=lambda e: list(e.path))
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path)
        raise VectorFileError(f"{path.name}: schema violation at {where or 'the top level'}")


# ---------------------------------------------------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------------------------------------------------


def manifest_files() -> list[Path]:
    """Every file the manifest covers: SPEC.md, the schema and the vector files."""
    return [SPEC, SCHEMA, *sorted(VECTORS.glob("*.json"))]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    files = {p.relative_to(CONFORMANCE).as_posix(): _sha256(p) for p in manifest_files()}
    digest = hashlib.sha256("".join(f"{name}\0{sha}\n" for name, sha in sorted(files.items())).encode()).hexdigest()
    standard = re.search(r"\*\*Version:\*\* (LDP-\d+\.\d+\.\d+)", SPEC.read_text(encoding="utf-8"))
    return {
        "comment": (
            "SHA-256 of every file of the Levuvo Database Profile (TAB-050). Regenerate with "
            "'python -m tests.support.conformance --write' and name the new digest in CHANGELOG.md."
        ),
        "format": "ldp-manifest/1",
        "standard_version": standard.group(1) if standard else None,
        "files": files,
        "digest": digest,
    }


def verify_manifest() -> None:
    """``MANIFEST.json`` matches every file, and ``CHANGELOG.md`` names its digest."""
    try:
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise VectorFileError("MANIFEST.json: not readable JSON") from None
    actual = build_manifest()
    changed = sorted(
        name
        for name in set(recorded.get("files", {})) | set(actual["files"])
        if recorded.get("files", {}).get(name) != actual["files"].get(name)
    )
    if changed:
        raise VectorFileError(f"MANIFEST.json: checksum differs for {changed} (an expectation changed)")
    if recorded.get("digest") != actual["digest"] or recorded.get("standard_version") != actual["standard_version"]:
        raise VectorFileError("MANIFEST.json: digest or standard_version differs")
    if f"LDP digest {actual['digest'][:12]}" not in CHANGELOG.read_text(encoding="utf-8"):
        raise VectorFileError(
            f"CHANGELOG.md does not name the current vectors ('LDP digest {actual['digest'][:12]}'): "
            "a changed expectation needs a CHANGELOG entry"
        )


def write_manifest() -> str:
    data = build_manifest()
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(data["digest"])


def spec_rules() -> dict[str, str]:
    """Rule ID -> level, from the rule table of ``SPEC.md``."""
    rules: dict[str, str] = {}
    for match in re.finditer(r"^\| ([A-Z]{2,4}-\d+) \| (MUST|SHOULD|INFO) \|", SPEC.read_text(encoding="utf-8"), re.M):
        rules[match.group(1)] = match.group(2)
    return rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or regenerate conformance/MANIFEST.json (TAB-050).")
    parser.add_argument("--write", action="store_true", help="regenerate the manifest")
    args = parser.parse_args(argv)
    if args.write:
        print(f"conformance: wrote MANIFEST.json; name 'LDP digest {write_manifest()[:12]}' in CHANGELOG.md")
        return 0
    try:
        vectors = load_all()
        verify_manifest()
    except VectorFileError as exc:
        print(f"conformance: {exc}", file=sys.stderr)
        return 1
    print(f"conformance: {len(vectors)} vectors, manifest consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
