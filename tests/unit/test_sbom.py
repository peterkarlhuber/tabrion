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
Provides-Extra: dev
Requires-Dist: psycopg[binary]<4,>=3.2; extra == "dev"
"""

WHEEL = """\
Wheel-Version: 1.0
Generator: hatchling 1.33.0
Root-Is-Purelib: true
Tag: py3-none-any
"""

SHA = "0123456789abcdef0123456789abcdef01234567"


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
        archive.writestr("tabrion-0.1.0.dist-info/WHEEL", WHEEL)
    return path


def _wheel_with(tmp_path: Path, metadata: str) -> Path:
    path = tmp_path / "other" / "tabrion-0.1.0-py3-none-any.whl"
    path.parent.mkdir()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("tabrion-0.1.0.dist-info/METADATA", metadata)
    return path


def test_document_shape(wheel: Path) -> None:
    doc = sbom.build_sbom(wheel, vcs_ref=SHA)
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.6"
    assert doc["serialNumber"].startswith("urn:uuid:")
    component = doc["metadata"]["component"]
    assert component["name"] == "tabrion"
    assert component["version"] == "0.1.0"
    assert component["hashes"] == [{"alg": "SHA-256", "content": sbom.sha256(wheel)}]
    assert component["licenses"] == [{"expression": "MIT"}]
    # Canonical purl encoding (review I-2): "+" and "@" encoded, ":" and "/" kept, as packageurl-python writes it.
    assert component["purl"] == (
        f"pkg:generic/tabrion@0.1.0?vcs_url=git%2Bhttps://github.com/peterkarlhuber/tabrion%40{SHA}"
    )
    assert "timestamp" not in doc["metadata"]


def test_build_backend_is_recorded(wheel: Path) -> None:
    """Review M-1: the wheel's bytes depend on the backend version, so the SBOM names it."""
    doc = sbom.build_sbom(wheel)
    tools = doc["metadata"]["tools"]["components"]
    assert {"type": "application", "name": "hatchling", "version": "1.33.0"} in tools
    properties = {(p["name"], p["value"]) for p in doc["metadata"]["component"]["properties"]}
    assert ("tabrion:build-backend", "hatchling 1.33.0") in properties


def test_serial_number_names_the_document(wheel: Path) -> None:
    """Review L-1: different documents get different serial numbers; the same document the same one."""
    plain, again = sbom.build_sbom(wheel), sbom.build_sbom(wheel)
    with_ref = sbom.build_sbom(wheel, vcs_ref=SHA)
    with_env = sbom.build_sbom(wheel, environment={"build": sbom.Installed("1.2.0")})
    assert plain["serialNumber"] == again["serialNumber"]
    assert len({plain["serialNumber"], with_ref["serialNumber"], with_env["serialNumber"]}) == 3
    # The timestamp does not change the serial number: one document, written at one time.
    assert sbom.build_sbom(wheel, epoch="1700000000")["serialNumber"] == plain["serialNumber"]


def test_invalid_vcs_ref_and_epoch_are_refused(wheel: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Review I-3 and I-5: a moving ref is refused, a bad SOURCE_DATE_EPOCH is a clear message, not a traceback."""
    for ref in ("main", "v0.1.0", SHA[:12], SHA.upper()):
        with pytest.raises(ValueError, match="40-character"):
            sbom.build_sbom(wheel, vcs_ref=ref)
    for epoch in ("soon", "-1", "1.5"):
        with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
            sbom.build_sbom(wheel, epoch=epoch)
    with pytest.raises(SystemExit) as exit_info:
        sbom.main([str(wheel), "--vcs-ref", "main"])
    assert exit_info.value.code == 2
    assert "40-character" in capsys.readouterr().err


def test_declared_dependencies_are_optional_and_without_self(wheel: Path) -> None:
    doc = sbom.build_sbom(wheel)
    by_name = {c["name"]: c for c in doc["components"]}
    assert sorted(by_name) == ["alembic", "psycopg", "sqlalchemy"]
    assert all(c["scope"] == "optional" for c in by_name.values())
    psycopg = {(p["name"], p["value"]) for p in by_name["psycopg"]["properties"]}
    assert psycopg == {
        ("tabrion:extra", "all"),
        ("tabrion:extra", "dev"),
        ("tabrion:extra", "postgres"),
        ("tabrion:specifier", "<4,>=3.2"),
        ("tabrion:requested-extra", "binary"),  # review L-2: psycopg[binary] of the dev extra
    }
    assert doc["dependencies"] == [{"ref": "tabrion@0.1.0", "dependsOn": ["alembic", "psycopg", "sqlalchemy"]}]


def test_core_dependency_is_required(tmp_path: Path) -> None:
    """Review L-3: a Requires-Dist without an extra marker is a required component."""
    path = _wheel_with(tmp_path, METADATA + "Requires-Dist: typing-extensions>=4\n")
    by_name = {c["name"]: c for c in sbom.build_sbom(path)["components"]}
    assert by_name["typing-extensions"]["scope"] == "required"
    assert by_name["alembic"]["scope"] == "optional"
    assert "typing-extensions" in sbom.build_sbom(path)["dependencies"][0]["dependsOn"]


def test_environment_resolves_versions(wheel: Path) -> None:
    commit = "f" * 40
    doc = sbom.build_sbom(
        wheel,
        environment={
            "sqlalchemy": sbom.Installed("2.0.40"),
            "build": sbom.Installed("1.2.0"),
            "tabrion": sbom.Installed("0.1.0"),
            "axentra": sbom.Installed("0.3.0", f"git+https://github.com/peterkarlhuber/axentra@{commit}"),
        },
    )
    by_name = {c["name"]: c for c in doc["components"]}
    assert by_name["sqlalchemy"]["purl"] == "pkg:pypi/sqlalchemy@2.0.40"
    assert by_name["sqlalchemy"]["bom-ref"] == "sqlalchemy@2.0.40"
    assert by_name["build"]["scope"] == "excluded"
    assert {"name": "tabrion:source", "value": "installed environment"} in by_name["build"]["properties"]
    # Review L-4: a Git-pinned sibling is not a PyPI package.
    assert by_name["axentra"]["purl"] == (
        f"pkg:generic/axentra@0.3.0?vcs_url=git%2Bhttps://github.com/peterkarlhuber/axentra%40{commit}"
    )
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


def test_vcs_url_from_direct_url_json() -> None:
    """Review L-4: PEP 610 ``direct_url.json`` with ``vcs_info`` gives ``<vcs>+<url>@<commit>``; anything else None."""
    commit = "a" * 40
    vcs = json.dumps({"url": "https://github.com/x/y", "vcs_info": {"vcs": "git", "commit_id": commit}})
    assert sbom.vcs_url_of(vcs) == f"git+https://github.com/x/y@{commit}"
    assert sbom.vcs_url_of(json.dumps({"url": "file:///tmp/x", "dir_info": {}})) is None
    assert sbom.vcs_url_of(None) is None
    assert sbom.vcs_url_of("not json") is None


def test_installed_environment_has_versions() -> None:
    found = sbom.installed()
    assert found["pytest"].version
    assert all(isinstance(item, sbom.Installed) for item in found.values())
