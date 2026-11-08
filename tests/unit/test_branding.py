"""TAB-006: the brand assets come from the supplied logo master, and the docs serve the same bytes.

``assets/branding/build.py`` derives every file; see ``assets/branding/README.md``. The sdist leaves out ``assets/``,
so these checks need a repository checkout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import zlib

import pytest

from tests.conftest import ROOT

BRANDING = ROOT / "assets" / "branding"
DOCS = ROOT / "docs" / "assets" / "branding"

pytestmark = pytest.mark.skipif(not BRANDING.is_dir(), reason="needs a repository checkout")

# sha256 of the committed logo master: Peter Karlhuber's file of 2026-11-08 with its metadata chunks removed (G-T1
# BR-2). The copy must never change.
MASTER = "tabrion-logo-master-2026-11-08.png"
MASTER_SHA256 = {MASTER: "e253dbf74d714222e4c00c43d5475757c771af124844c66fc3ed6d5d8d796997"}
# The supplied file (planning folder, logo/) keeps its C2PA (caBX) and eXIf chunks; its sha256 is
# b3e5f441c9e7200153f1d4b437100c72a3da4445f23ef7e0f52c9728850ff7e1.
# sha256 of the decompressed image data (all IDAT chunks) of the supplied file: the stripped copy has the same pixels.
SUPPLIED_PIXELS_SHA256 = "4342c95fbbe04af6a53a5ded28153969ebf6f092f245dcb6171c0adfc8a31e12"

# sha256 of the brand kit scripts, vendored unchanged from nodestro-studio/scripts/.
VENDORED_SHA256 = {
    "derive_branding.py": "278b67212c204e445f351042025468e74846d36c26eac0c081f5238289484135",
    "social_preview.py": "93f2573af31be3f567ec5b000a6bf15fc6164e974e1df404db4398319f21e189",
}

KEPT = (
    "tabrion-logo.png",
    "tabrion-logo-on-dark.png",
    "tabrion-mark-on-dark-128.png",
    "favicon.ico",
    "favicon-32.png",
    "apple-touch-icon.png",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", sorted(MASTER_SHA256))
def test_the_logo_master_is_unchanged(name: str) -> None:
    assert _sha256(BRANDING / name) == MASTER_SHA256[name]


def _chunks(path: Path) -> list[tuple[bytes, bytes]]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    chunks, i = [], 8
    while i < len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        chunks.append((data[i + 4 : i + 8], data[i + 8 : i + 8 + length]))
        i += 12 + length
    return chunks


def test_the_master_carries_no_metadata_and_the_supplied_pixels() -> None:
    """BR-2: only the chunks needed to show the image; the image data is the supplied file's, byte for byte."""
    chunks = _chunks(BRANDING / MASTER)
    assert {kind for kind, _ in chunks} == {b"IHDR", b"sRGB", b"IDAT", b"IEND"}
    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    assert hashlib.sha256(pixels).hexdigest() == SUPPLIED_PIXELS_SHA256


@pytest.mark.parametrize("name", sorted(VENDORED_SHA256))
def test_the_brand_kit_scripts_are_vendored_unchanged(name: str) -> None:
    assert _sha256(BRANDING / name) == VENDORED_SHA256[name]


@pytest.mark.parametrize("name", KEPT)
def test_docs_serve_the_same_brand_assets(name: str) -> None:
    assert (DOCS / name).read_bytes() == (BRANDING / name).read_bytes()


def _png_size(path: Path) -> tuple[int, int]:
    # PNG IHDR: width and height are the big-endian 32-bit integers at bytes 16..24.
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def test_derived_masters_and_social_preview_sizes() -> None:
    assert _png_size(BRANDING / "tabrion-logo-transparent.png") == (2048, 640)
    assert _png_size(BRANDING / "tabrion-logo-white.png") == (2048, 640)
    assert _png_size(BRANDING / "social-preview.png") == (1280, 640)


def test_readme_and_docs_use_the_lockups() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    raw = "https://raw.githubusercontent.com/peterkarlhuber/tabrion/master/assets/branding/"
    # Absolute URLs: the README is also the PyPI description (G-T1 BR-4).
    assert f'srcset="{raw}tabrion-logo-on-dark.png"' in readme
    assert f'src="{raw}tabrion-logo.png"' in readme
    assert "[TRADEMARKS.md](TRADEMARKS.md)" in readme
    assert (ROOT / "TRADEMARKS.md").is_file()
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "logo: assets/branding/tabrion-mark-on-dark-128.png" in mkdocs
    assert "favicon: assets/branding/favicon-32.png" in mkdocs
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert "tabrion-logo.png#only-light" in index
    assert "tabrion-logo-on-dark.png#only-dark" in index
