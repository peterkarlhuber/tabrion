"""TAB-T-V01..V04 and the TAB-050 checks of the vector files themselves (API contract sections 10 and 11).

V01 every file loads and validates against the JSON Schema; V02 the manifest matches and the CHANGELOG names its
digest; V03 a damaged file is refused, naming the file but not the content; V04 a duplicate ID is refused. Also:
every MUST rule of SPEC.md has a vector, every vector names a rule of SPEC.md with the same level, and no vector
holds the redaction marker outside a redaction vector.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from tests.support import conformance
from tests.support.conformance import AREAS, VectorFileError, load_all, parse, spec_rules, validate_schema

pytestmark = pytest.mark.conformance

ACTIONS = {
    "open_new_file",
    "open_existing",
    "inspect_pragmas",
    "concurrent_read_modify_write",
    "write_in_read_transaction",
    "migrate",
    "session_settings",
    "role_report",
    "grant_runtime_role",
    "lock_acquire",
    "lock_connection_lost",
    "extension_status",
    "classify_error",
    "redact_error",
    "split_sql",
}
"""The closed action list of the schema (contract section 10.2)."""


def _require_jsonschema() -> None:
    """The schema check is an acceptance criterion: in CI a missing ``jsonschema`` fails, locally it skips (L-7)."""
    if os.environ.get("CI") or os.environ.get("TABRION_REQUIRE_POSTGRES"):
        importlib.import_module("jsonschema")
    else:
        pytest.importorskip("jsonschema")


def _file(area: str = "sql-split") -> tuple[Path, dict[str, Any]]:
    path = conformance.VECTORS / f"{area}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_v01_every_area_has_a_file_and_every_file_loads() -> None:
    assert sorted(p.stem for p in conformance.VECTORS.glob("*.json")) == sorted(AREAS)
    vectors = load_all()
    assert len(vectors) == len({v.id for v in vectors})
    assert {v.action for v in vectors} <= ACTIONS


@pytest.mark.parametrize("area", AREAS)
def test_v01_every_file_validates_against_the_json_schema(area: str) -> None:
    _require_jsonschema()
    validate_schema(conformance.VECTORS / f"{area}.json")


def test_schema_is_the_contract_schema() -> None:
    """``schema/vector-file.schema.json`` is the schema of API contract section 10.1, unchanged."""
    contract = (conformance.ROOT / "docs" / "api" / "contract.md").read_text(encoding="utf-8")
    start = contract.index("```json", contract.index("### 10.1 JSON Schema")) + len("```json")
    expected = json.loads(contract[start : contract.index("```", start)])
    assert json.loads(conformance.SCHEMA.read_text(encoding="utf-8")) == expected
    assert sorted(
        expected["properties"]["vectors"]["items"]["properties"]["action"]["properties"]["name"]["enum"]
    ) == sorted(ACTIONS)


def test_v02_manifest_matches_and_changelog_names_the_digest() -> None:
    conformance.verify_manifest()


def test_v02_a_changed_expectation_fails_the_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy_root = tmp_path / "conformance"
    shutil.copytree(conformance.CONFORMANCE, copy_root)
    path = copy_root / "vectors" / "sql-split.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["vectors"][0]["expect"]["statements"] = ["SELECT 1"]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(conformance, "CONFORMANCE", copy_root)
    monkeypatch.setattr(conformance, "VECTORS", copy_root / "vectors")
    monkeypatch.setattr(conformance, "SCHEMA", copy_root / "schema" / "vector-file.schema.json")
    monkeypatch.setattr(conformance, "SPEC", copy_root / "SPEC.md")
    monkeypatch.setattr(conformance, "MANIFEST", copy_root / "MANIFEST.json")
    with pytest.raises(VectorFileError, match=r"checksum differs for \['conformance/vectors/sql-split.json'\]"):
        conformance.verify_manifest()
    # Regenerating the manifest is not enough: the CHANGELOG must name the new digest.
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(conformance.CHANGELOG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(conformance, "CHANGELOG", changelog)
    digest = conformance.write_manifest()
    with pytest.raises(VectorFileError, match=r"CHANGELOG\.md does not name the current vectors"):
        conformance.verify_manifest()
    # Naming the new digest under the old version is not enough either: one LDP version has one digest (L-5).
    changelog.write_text(
        changelog.read_text(encoding="utf-8") + f"\n- LDP-0.1.0 digest {digest[:12]}\n", encoding="utf-8"
    )
    with pytest.raises(VectorFileError, match=r"more than one digest for LDP-0\.1\.0"):
        conformance.verify_manifest()
    # A new LDP version in SPEC.md (and its Changes entry) and its digest in the CHANGELOG pass.
    spec = copy_root / "SPEC.md"
    text = spec.read_text(encoding="utf-8").replace("**Version:** LDP-0.1.0", "**Version:** LDP-1.0.0")
    spec.write_text(
        text.replace("## Changes\n\n", "## Changes\n\n- **LDP-1.0.0**: LDP-SPL-001 changed; 33 rules.\n"),
        encoding="utf-8",
    )
    for area in AREAS:
        vector_file = copy_root / "vectors" / f"{area}.json"
        vector_file.write_text(
            vector_file.read_text(encoding="utf-8").replace("LDP-0.1.0", "LDP-1.0.0"), encoding="utf-8"
        )
    digest = conformance.write_manifest()
    changelog.write_text(
        changelog.read_text(encoding="utf-8") + f"\n- LDP-1.0.0 digest {digest[:12]}\n", encoding="utf-8"
    )
    conformance.verify_manifest()


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("| SQP-8 | MAY | sqlite-profile | x | y |", "level 'MAY' is not one of"),
        ("|SQP-8|MUST| sqlite-profile | x | y |", "does not count 34 rules"),
        ("| SQP-1 | MUST | sqlite-profile | again | y |", "SQP-1: duplicate rule"),
    ],
)
def test_spec_rules_refuses_unknown_rows(row: str, message: str) -> None:
    """Review L-4: every rule row counts; another level, a duplicate or a wrong rule count fails."""
    text = conformance.SPEC.read_text(encoding="utf-8")
    anchor = "| SQP-1 | MUST |"
    text = text.replace(anchor, row + "\n" + anchor, 1)
    with pytest.raises(VectorFileError, match=message):
        spec_rules(text)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda d: d.update(format="ldp-vectors/2"), "format"),
        (lambda d: d.update(area="sqlite-profile"), "area"),
        (lambda d: d["vectors"][0].pop("expect"), "LDP-SPL-001: a required field is missing"),
        (lambda d: d["vectors"][1].update(id="LDP-S-1"), "vector #1: the ID does not match"),
        (lambda d: d["vectors"][0].update(engine="mysql"), "LDP-SPL-001: unknown level or engine"),
        (lambda d: d.update(vectors=[]), "no vectors"),
    ],
)
def test_v03_a_damaged_file_is_refused_without_its_content(damage: Any, message: str) -> None:
    path, data = _file()
    damaged = copy.deepcopy(data)
    damage(damaged)
    with pytest.raises(VectorFileError, match=message) as info:
        parse(path, damaged)
    assert "SELECT" not in str(info.value), "the error must not show the vector's content"
    assert "sql-split.json" in str(info.value)


def test_v03_unreadable_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sql-split.json"
    path.write_text('{"format": "ldp-vectors/1", "vectors": [SECRET-4711', encoding="utf-8")
    with pytest.raises(VectorFileError, match="not readable JSON") as info:
        conformance._read(path)
    assert "SECRET" not in str(info.value)


def test_v03_schema_violation_is_reported(tmp_path: Path) -> None:
    _require_jsonschema()
    _, data = _file()
    data["vectors"][0]["unknown_field"] = 1
    path = tmp_path / "sql-split.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VectorFileError, match="schema violation at vectors/0"):
        validate_schema(path)


def test_v04_a_duplicate_id_is_refused() -> None:
    path, data = _file()
    data["vectors"][1]["id"] = data["vectors"][0]["id"]
    with pytest.raises(VectorFileError, match="LDP-SPL-001: duplicate ID"):
        parse(path, data)


def test_every_must_rule_has_a_vector_and_every_vector_a_rule() -> None:
    rules = spec_rules()
    vectors = load_all()
    assert rules, "no rule table in SPEC.md"
    covered = {v.rule for v in vectors}
    assert sorted(r for r, level in rules.items() if level == "MUST" and r not in covered) == []
    assert sorted(r for r in rules if r not in covered) == [], "every rule should have a vector"
    for v in vectors:
        assert v.rule in rules, f"{v.id}: rule {v.rule} is not in SPEC.md"
        assert v.level == rules[v.rule], f"{v.id}: level {v.level}, but {v.rule} is {rules[v.rule]}"
        assert v.id.split("-")[1] == v.rule.split("-")[0], f"{v.id}: the ID prefix does not match the rule area"
        assert v.provenance, f"{v.id}: no provenance"


def test_vectors_hold_no_credentials_and_the_marker_only_in_redaction() -> None:
    for v in load_all():
        text = json.dumps([v.args, v.setup, v.expect])
        assert "password" not in text.lower(), v.id
        if "SECRET-4711" in text:
            assert v.action == "redact_error" and "SECRET-4711" in v.expect["absent"], v.id


def test_every_hashed_file_is_checked_out_with_lf() -> None:
    """The manifest hashes raw bytes: ``.gitattributes`` pins LF for every hashed file, also under
    ``core.autocrlf=true`` on Windows (review of #27, H-2)."""
    if not (conformance.ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("not a Git checkout")
    names = [name for name, _ in conformance.manifest_files()]
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *names],
        cwd=conformance.ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    eol = dict(line.rsplit(": eol: ", 1) for line in result.stdout.splitlines())
    assert eol == dict.fromkeys(names, "lf")
    for _, path in conformance.manifest_files():
        assert b"\r" not in path.read_bytes(), path


def test_standard_version_matches_spec_and_manifest() -> None:
    manifest = json.loads(conformance.MANIFEST.read_text(encoding="utf-8"))
    for area in AREAS:
        _, data = _file(area)
        assert data["standard_version"] == manifest["standard_version"], area


def test_conformance_is_not_in_the_wheel_config() -> None:
    """The vectors are internal (K-35): the wheel packages only ``src/tabrion`` (the built wheel: test_artifacts)."""
    pyproject = (conformance.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.hatch.build.targets.wheel]\npackages = ["src/tabrion"]' in pyproject
