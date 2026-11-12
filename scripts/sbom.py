#!/usr/bin/env python3
"""Write a CycloneDX 1.6 SBOM (JSON) for a built Tabrion wheel (QA-8, plan document 07 TM-10).

Standard library only, so it runs in the CI build job, which installs nothing but ``build``, ``pytest`` and
``twine``. The SBOM describes:

* ``metadata.component``: the wheel itself (name, version, SHA-256 of the file, license, the Git repository and,
  with ``--vcs-ref``, the full commit SHA it was built from). Tabrion is not on PyPI (ADR 0009), so its package URL
  is ``pkg:generic`` with a ``vcs_url`` qualifier, not ``pkg:pypi``;
* ``metadata.tools``: this script and the build backend that wrote the wheel (the ``Generator`` of its ``WHEEL``
  file, for example ``hatchling 1.33.0``): the wheel's bytes are reproducible only for that backend version;
* ``components``: every ``Requires-Dist`` of the wheel, ``scope: required`` for a core dependency and
  ``optional`` for one behind an extra (the core needs nothing today, TAB-T-180), with the Tabrion extras that pull
  it in, its version range and the extras it requests of the dependency (``psycopg[binary]``) as properties. With
  ``--environment``, the distributions installed in the running interpreter are listed as well, with their exact
  versions, and a declared dependency that is installed is resolved to that version. An installed distribution
  that came from a VCS (its ``direct_url.json`` has ``vcs_info``, as for a Git-pinned sibling) gets a
  ``pkg:generic`` URL with the commit, every other one ``pkg:pypi``;
* ``dependencies``: the wheel depends on its declared components.

The output is deterministic for the same wheel and arguments: keys and lists are sorted, the serial number is a UUID
derived from the canonical document (without the serial number and the timestamp), so two different documents never
share one, and ``metadata.timestamp`` is written only when ``SOURCE_DATE_EPOCH`` is set (as in a reproducible build).
Two builds with the same hash give the same SBOM.

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
from typing import Any, NamedTuple
import urllib.parse
import uuid
import zipfile

REPOSITORY = "https://github.com/peterkarlhuber/tabrion"
SPEC_VERSION = "1.6"
TOOL = {"type": "application", "name": "tabrion scripts/sbom.py"}

_REQUIREMENT = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>[^;]*)")
_EXTRA = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_CORE = "(core)"


class Installed(NamedTuple):
    """An installed distribution: its version and, when it was installed from a VCS, ``<vcs>+<url>@<commit>``."""

    version: str
    vcs_url: str | None = None


def purl(name: str, version: str, vcs_url: str | None = None) -> str:
    """A package URL in canonical form: ``pkg:pypi`` or, for a VCS install, ``pkg:generic`` with ``vcs_url``.

    The qualifier value is percent-encoded as the purl specification and ``packageurl-python`` do it (``+`` and
    ``@`` encoded, ``:`` and ``/`` kept).
    """
    if vcs_url is None:
        return f"pkg:pypi/{name}@{version}"
    return f"pkg:generic/{name}@{version}?vcs_url={urllib.parse.quote(vcs_url, safe=':/')}"


def check_commit(ref: str) -> str:
    """A full, lowercase 40-character commit SHA (the SHA-pin rule of ADR 0009); a branch name or tag would move."""
    if not _COMMIT.fullmatch(ref):
        raise ValueError(f"--vcs-ref must be a full 40-character lowercase commit SHA, not {ref!r}")
    return ref


def check_epoch(value: str) -> int:
    """``SOURCE_DATE_EPOCH`` as a non-negative integer of seconds."""
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"SOURCE_DATE_EPOCH must be a non-negative integer of seconds, not {value!r}")
    return int(value)


def normalize(name: str) -> str:
    """PEP 503 name normalization (``Foo_Bar`` and ``foo-bar`` are the same project)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dist_info(wheel: Path, member: str) -> email.message.Message | None:
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(f".dist-info/{member}") and n.count("/") == 1]
        if len(names) > 1:
            raise SystemExit(f"{wheel.name}: expected one .dist-info/{member}, found {len(names)}")
        if not names:
            return None
        return email.parser.Parser().parsestr(archive.read(names[0]).decode("utf-8"))


def wheel_metadata(wheel: Path) -> email.message.Message:
    meta = _dist_info(wheel, "METADATA")
    if meta is None:
        raise SystemExit(f"{wheel.name}: expected one .dist-info/METADATA, found 0")
    return meta


def build_backend(wheel: Path) -> tuple[str, str] | None:
    """``(name, version)`` from the ``Generator`` of the wheel's ``WHEEL`` file, e.g. ``("hatchling", "1.33.0")``."""
    info = _dist_info(wheel, "WHEEL")
    generator = (info.get("Generator") if info is not None else None) or ""
    name, _, version = generator.strip().partition(" ")
    return (name, version.strip()) if name and version.strip() else None


def declared_dependencies(requires: Iterable[str], own_name: str) -> dict[str, dict[str, set[str]]]:
    """``{normalized name: {"extras": {...}, "specifiers": {...}, "requested": {...}}}`` for every ``Requires-Dist``
    but the package's own extras (``tabrion[sqlalchemy]; extra == "migrations"``). ``extras`` are the Tabrion extras
    that pull the dependency in (``(core)`` without an extra marker), ``requested`` the dependency's own extras."""
    found: dict[str, dict[str, set[str]]] = {}
    for line in requires:
        match = _REQUIREMENT.match(line)
        if match is None:
            raise SystemExit(f"unparseable Requires-Dist: {line!r}")
        name = normalize(match["name"])
        if name == normalize(own_name):
            continue
        entry = found.setdefault(name, {"extras": set(), "specifiers": set(), "requested": set()})
        marker = line.partition(";")[2]
        entry["extras"].update(_EXTRA.findall(marker) or [_CORE])
        entry["requested"].update(e.strip() for e in (match["extras"] or "").split(",") if e.strip())
        spec = match["spec"].strip().strip("()").replace(" ", "")
        if spec:
            entry["specifiers"].add(spec)
    return found


def vcs_url_of(direct_url: str | None) -> str | None:
    """``<vcs>+<url>@<commit>`` from a ``direct_url.json`` (PEP 610) with ``vcs_info``, else ``None``."""
    if not direct_url:
        return None
    try:
        data = json.loads(direct_url)
    except ValueError:
        return None
    info = data.get("vcs_info") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not isinstance(data.get("url"), str):
        return None
    vcs, commit = info.get("vcs"), info.get("commit_id")
    if not isinstance(vcs, str) or not isinstance(commit, str):
        return None
    return f"{vcs}+{data['url']}@{commit}"


def installed() -> dict[str, Installed]:
    """``{normalized name: Installed}`` of the distributions visible to this interpreter."""
    result: dict[str, Installed] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            result.setdefault(normalize(name), Installed(dist.version, vcs_url_of(dist.read_text("direct_url.json"))))
    return result


def _properties(pairs: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in sorted(pairs)]


def build_sbom(
    wheel: Path,
    *,
    vcs_ref: str | None = None,
    environment: dict[str, Installed] | None = None,
    epoch: str | None = None,
) -> dict[str, Any]:
    """The SBOM as a dict; ``environment`` is ``installed()`` (or a stand-in in tests), ``epoch`` a
    ``SOURCE_DATE_EPOCH`` value. Raises ``ValueError`` for a ``vcs_ref`` that is not a full commit SHA or an
    ``epoch`` that is not a non-negative integer."""
    if vcs_ref is not None:
        check_commit(vcs_ref)
    seconds = check_epoch(epoch) if epoch is not None else None
    meta = wheel_metadata(wheel)
    name, version = meta["Name"], meta["Version"]
    digest = sha256(wheel)
    vcs_url = f"git+{REPOSITORY}" + (f"@{vcs_ref}" if vcs_ref else "")
    own_ref = f"{normalize(name)}@{version}"
    backend = build_backend(wheel)
    own_properties = [
        ("tabrion:wheel", wheel.name),
        *([("tabrion:vcs-ref", vcs_ref)] if vcs_ref else []),
        *([("tabrion:build-backend", f"{backend[0]} {backend[1]}")] if backend else []),
    ]
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": own_ref,
        "name": name,
        "version": version,
        "purl": purl(normalize(name), version, vcs_url),
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
        found = env.get(dep)
        entry: dict[str, Any] = {
            "type": "library",
            "bom-ref": f"{dep}@{found.version}" if found else dep,
            "name": dep,
            "scope": "required" if _CORE in info["extras"] else "optional",
            "properties": _properties(
                [("tabrion:extra", extra) for extra in info["extras"]]
                + [("tabrion:specifier", spec) for spec in info["specifiers"]]
                + [("tabrion:requested-extra", extra) for extra in info["requested"]]
            ),
        }
        if found:
            entry["version"] = found.version
            entry["purl"] = purl(dep, found.version, found.vcs_url)
        components[dep] = entry
    for dep, found in env.items():
        if dep == normalize(name) or dep in components:
            continue
        components[dep] = {
            "type": "library",
            "bom-ref": f"{dep}@{found.version}",
            "name": dep,
            "version": found.version,
            "purl": purl(dep, found.version, found.vcs_url),
            "scope": "excluded",
            "properties": _properties([("tabrion:source", "installed environment")]),
        }

    tools = [TOOL]
    if backend:
        tools.append({"type": "application", "name": backend[0], "version": backend[1]})
    metadata: dict[str, Any] = {"tools": {"components": tools}, "component": component}
    declared = sorted(c["bom-ref"] for c in components.values() if c["scope"] != "excluded")
    doc: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": metadata,
        "components": [components[k] for k in sorted(components)],
        "dependencies": [{"ref": own_ref, "dependsOn": declared}],
    }
    # The serial number names this document: a UUID of its canonical form without serial number and timestamp.
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    doc["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{REPOSITORY}#{canonical}')}"
    if seconds is not None:
        metadata["timestamp"] = dt.datetime.fromtimestamp(seconds, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    parser.add_argument("--vcs-ref", help="the full 40-character commit SHA the wheel was built from")
    parser.add_argument("--environment", action="store_true", help="also list this interpreter's distributions")
    args = parser.parse_args(argv)
    try:
        sbom = build_sbom(
            args.wheel,
            vcs_ref=args.vcs_ref,
            environment=installed() if args.environment else None,
            epoch=os.environ.get("SOURCE_DATE_EPOCH") or None,
        )
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
