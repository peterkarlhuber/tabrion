"""QA-8: ``scripts/sbom.py`` writes a deterministic CycloneDX 1.6 SBOM for a wheel (standard library only)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import zipfile

import pytest

from tests.conftest import ROOT

METADATA = """\
Metadata-Version: 2.4
Name: tabrion
Version: 0.1.0
License-Expression: MIT
Requires-Python: >=3.11
Provides-Extra: postgres
Requires-Dist: psycopg<4,>=3.2; extra == "postgres"
Provides-Extra: sqlalchemy
Requires-Dist: sqlalchemy<2.2,>=2.0.30; extra == "sqlalchemy"
Provides-Extra: migrations
Requires-Dist: alembic<2,>=1.13; extra == "migrations"
Requires-Dist: tabrion[sqlalchemy]; extra == "migrations"
Provides-Extra: all
Requires-Dist: psycopg<4,>=3.2; extra == "all"
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tabrion_sbom", ROOT / "scripts" / "sbom.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sbom = _load()


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    path = tmp_path / "tabrion-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("tabrion/__init__.py", "")
        archive.writestr("tabrion-0.1.0.dist-info/METADATA", METADATA)
    return path


def test_document_shape(wheel: Path) -> None:
    doc = sbom.build_sbom(wheel, vcs_ref="0" * 40)
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.6"
    assert doc["serialNumber"].startswith("urn:uuid:")
    component = doc["metadata"]["component"]
    assert component["name"] == "tabrion"
    assert component["version"] == "0.1.0"
    assert component["hashes"] == [{"alg": "SHA-256", "content": sbom.sha256(wheel)}]
    assert component["licenses"] == [{"expression": "MIT"}]
    assert component["purl"].startswith("pkg:generic/tabrion@0.1.0?vcs_url=git+https%3A//github.com/")
    assert component["purl"].endswith("%40" + "0" * 40)
    assert "timestamp" not in doc["metadata"]


def test_declared_dependencies_are_optional_and_without_self(wheel: Path) -> None:
    doc = sbom.build_sbom(wheel)
    by_name = {c["name"]: c for c in doc["components"]}
    assert sorted(by_name) == ["alembic", "psycopg", "sqlalchemy"]
    assert all(c["scope"] == "optional" for c in by_name.values())
    psycopg = {(p["name"], p["value"]) for p in by_name["psycopg"]["properties"]}
    assert psycopg == {("tabrion:extra", "all"), ("tabrion:extra", "postgres"), ("tabrion:specifier", "<4,>=3.2")}
    assert doc["dependencies"] == [{"ref": "tabrion@0.1.0", "dependsOn": ["alembic", "psycopg", "sqlalchemy"]}]


def test_environment_resolves_versions(wheel: Path) -> None:
    doc = sbom.build_sbom(wheel, environment={"sqlalchemy": "2.0.40", "build": "1.2.0", "tabrion": "0.1.0"})
    by_name = {c["name"]: c for c in doc["components"]}
    assert by_name["sqlalchemy"]["purl"] == "pkg:pypi/sqlalchemy@2.0.40"
    assert by_name["sqlalchemy"]["bom-ref"] == "sqlalchemy@2.0.40"
    assert by_name["build"]["scope"] == "excluded"
    assert "tabrion" not in by_name
    assert "sqlalchemy@2.0.40" in doc["dependencies"][0]["dependsOn"]


def test_deterministic_with_source_date_epoch(wheel: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    assert sbom.main([str(wheel), "-o", str(first)]) == 0
    assert sbom.main([str(wheel), "-o", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text())["metadata"]["timestamp"] == "2023-11-14T22:13:20Z"


def test_unparseable_requirement_fails() -> None:
    with pytest.raises(SystemExit):
        sbom.declared_dependencies([";;"], "tabrion")
