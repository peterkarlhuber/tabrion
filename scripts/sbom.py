#!/usr/bin/env python3
"""Write a CycloneDX 1.6 SBOM (JSON) for a built Tabrion wheel (QA-8, plan document 07 TM-10).

Standard library only, so it runs in the CI build job, which installs nothing but ``build``, ``pytest`` and
``twine``. The SBOM describes:

* ``metadata.component``: the wheel itself (name, version, SHA-256 of the file, license, the Git repository and,
  with ``--vcs-ref``, the commit it was built from). Tabrion is not on PyPI (ADR 0009), so its package URL is
  ``pkg:generic`` with a ``vcs_url`` qualifier, not ``pkg:pypi``;
* ``components``: every ``Requires-Dist`` of the wheel, each with ``scope: optional`` (the core needs nothing,
  TAB-T-180), the extras that pull it in and its version range as properties. With ``--environment``, the
  distributions installed in the running interpreter are listed as well, with their exact versions
  (``pkg:pypi``), and a declared dependency that is installed is resolved to that version;
* ``dependencies``: the wheel depends on its declared components.

The output is deterministic for the same wheel and arguments: keys and lists are sorted, the serial number is a UUID
derived from the wheel's hash, and ``metadata.timestamp`` is written only when ``SOURCE_DATE_EPOCH`` is set (as in a
reproducible build). Two builds with the same hash give the same SBOM.

Usage: ``python scripts/sbom.py dist/tabrion-<version>-py3-none-any.whl [-o sbom.cdx.json] [--vcs-ref <sha>]
[--environment]``.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
import datetime as dt
import email.parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid
import zipfile

REPOSITORY = "https://github.com/peterkarlhuber/tabrion"
SPEC_VERSION = "1.6"
TOOL = {"type": "application", "name": "tabrion scripts/sbom.py"}

_REQUIREMENT = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>[^;]*)")
_EXTRA = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


def normalize(name: str) -> str:
    """PEP 503 name normalization (``Foo_Bar`` and ``foo-bar`` are the same project)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_metadata(wheel: Path) -> email.message.Message:
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
        if len(names) != 1:
            raise SystemExit(f"{wheel.name}: expected one .dist-info/METADATA, found {len(names)}")
        return email.parser.Parser().parsestr(archive.read(names[0]).decode("utf-8"))


def declared_dependencies(requires: Iterable[str], own_name: str) -> dict[str, dict[str, set[str]]]:
    """``{normalized name: {"extras": {...}, "specifiers": {...}}}`` for every ``Requires-Dist`` but the package's
    own extras (``tabrion[sqlalchemy]; extra == "migrations"``)."""
    found: dict[str, dict[str, set[str]]] = {}
    for line in requires:
        match = _REQUIREMENT.match(line)
        if match is None:
            raise SystemExit(f"unparseable Requires-Dist: {line!r}")
        name = normalize(match["name"])
        if name == normalize(own_name):
            continue
        entry = found.setdefault(name, {"extras": set(), "specifiers": set()})
        marker = line.partition(";")[2]
        entry["extras"].update(_EXTRA.findall(marker) or ["(core)"])
        spec = match["spec"].strip().strip("()").replace(" ", "")
        if spec:
            entry["specifiers"].add(spec)
    return found


def installed() -> dict[str, str]:
    """``{normalized name: version}`` of the distributions visible to this interpreter."""
    result: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            result.setdefault(normalize(name), dist.version)
    return result


def _properties(pairs: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in sorted(pairs)]


def build_sbom(
    wheel: Path,
    *,
    vcs_ref: str | None = None,
    environment: dict[str, str] | None = None,
    epoch: str | None = None,
) -> dict[str, Any]:
    """The SBOM as a dict; ``environment`` is ``installed()`` (or a stand-in in tests), ``epoch`` a
    ``SOURCE_DATE_EPOCH`` value."""
    meta = wheel_metadata(wheel)
    name, version = meta["Name"], meta["Version"]
    digest = sha256(wheel)
    vcs_url = f"git+{REPOSITORY}" + (f"@{vcs_ref}" if vcs_ref else "")
    own_ref = f"{normalize(name)}@{version}"
    own_properties = [("tabrion:wheel", wheel.name), *([("tabrion:vcs-ref", vcs_ref)] if vcs_ref else [])]
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": own_ref,
        "name": name,
        "version": version,
        "purl": f"pkg:generic/{normalize(name)}@{version}?vcs_url={vcs_url.replace(':', '%3A').replace('@', '%40')}",
        "hashes": [{"alg": "SHA-256", "content": digest}],
        "externalReferences": [{"type": "vcs", "url": REPOSITORY}],
        "properties": _properties(own_properties),
    }
    license_expression = meta.get("License-Expression")
    if license_expression:
        component["licenses"] = [{"expression": license_expression}]

    env = environment or {}
    components: dict[str, dict[str, Any]] = {}
    for dep, info in declared_dependencies(meta.get_all("Requires-Dist") or [], name).items():
        entry: dict[str, Any] = {
            "type": "library",
            "bom-ref": f"{dep}@{env[dep]}" if dep in env else dep,
            "name": dep,
            "scope": "optional",
            "properties": _properties(
                [("tabrion:extra", extra) for extra in info["extras"]]
                + [("tabrion:specifier", spec) for spec in info["specifiers"]]
            ),
        }
        if dep in env:
            entry["version"] = env[dep]
            entry["purl"] = f"pkg:pypi/{dep}@{env[dep]}"
        components[dep] = entry
    for dep, dep_version in env.items():
        if dep == normalize(name) or dep in components:
            continue
        components[dep] = {
            "type": "library",
            "bom-ref": f"{dep}@{dep_version}",
            "name": dep,
            "version": dep_version,
            "purl": f"pkg:pypi/{dep}@{dep_version}",
            "scope": "excluded",
            "properties": _properties([("tabrion:source", "build environment")]),
        }

    metadata: dict[str, Any] = {"tools": {"components": [TOOL]}, "component": component}
    if epoch is not None:
        metadata["timestamp"] = dt.datetime.fromtimestamp(int(epoch), dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    declared = sorted(c["bom-ref"] for c in components.values() if c["scope"] == "optional")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{REPOSITORY}#{digest}')}",
        "version": 1,
        "metadata": metadata,
        "components": [components[k] for k in sorted(components)],
        "dependencies": [{"ref": own_ref, "dependsOn": declared}],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    parser.add_argument("--vcs-ref", help="the commit SHA the wheel was built from")
    parser.add_argument("--environment", action="store_true", help="also list this interpreter's distributions")
    args = parser.parse_args(argv)
    sbom = build_sbom(
        args.wheel,
        vcs_ref=args.vcs_ref,
        environment=installed() if args.environment else None,
        epoch=os.environ.get("SOURCE_DATE_EPOCH") or None,
    )
    text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
