"""Derive the Tabrion brand assets in this folder from Peter's logo master.

The supplied master ``tabrion-logo-master-2026-11-08.png`` (kept byte for byte) is a 1254 x 1254 RGB image on white
with two lockups: "Tabrion" at the top and "Tabrion STUDIO" at the bottom. The shared brand kit script
``derive_branding.py`` (vendored unchanged, like ``social_preview.py``) expects a 2048 x 640 transparent master with
the symbol left and a pure black wordmark right, plus the same artwork flattened on white. This wrapper prepares
those two files from the top lockup, then does what the Orkexa wrapper does: run ``derive_branding.py``, keep the
files this repository uses, render the 1280 x 640 social preview and copy the web assets into
``docs/assets/branding/`` (MkDocs serves only files below ``docs/``).

Preparing the transparent master (nothing is redrawn; every step is a pixel operation on the supplied artwork):

1. Crop the top lockup with a small margin. This leaves out the Studio lockup and the faint ghost of old letters
   right of the wordmark.
2. Clean the background: every pixel at or above ``BACKGROUND`` (the ghost and the paper noise are 247-255) is
   white.
3. Upscale with Lanczos so the lockup fills the 2048 x 640 canvas.
4. Knock out the white: each pixel takes the colour of the nearest solid area of its own shape (the black top
   layer, the dark grey and the light grey layer, the wordmark) and the alpha that reproduces its grey value over
   white. The wordmark is pure black with alpha, as ``derive_branding.py`` requires.

``--studio`` also writes the "Tabrion STUDIO" lockup as an RGB master on clean white for the later Studio work.
Run from the repository root with Pillow and NumPy::

    uv run --with pillow==12.3.0 --with numpy==2.5.3 python assets/branding/build.py [--studio OUT.png]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs" / "assets" / "branding"

sys.path.insert(0, str(HERE))
import derive_branding  # noqa: E402  (vendored next to this file)
import social_preview  # noqa: E402  (vendored next to this file)

PRODUCT = "tabrion"
SUPPLIED = HERE / "tabrion-logo-master-2026-11-08.png"

#: Crop boxes (left, top, right, bottom) in the supplied 1254 x 1254 master: the artwork plus a margin.
TOP_LOCKUP = (96, 370, 988, 600)
STUDIO_LOCKUP = (94, 798, 1140, 952)
#: Paper white: the ghost letters and the background noise lie at 247-255.
BACKGROUND = 247
#: Solid areas are darker than this; the lightest layer is about 204, flat paper noise lies above 235.
SOLID_MAX = 230
#: Width of the lockup in the 2048 x 640 transparent master.
MASTER_WIDTH = 1900

#: Brand kit registration, made at run time so the vendored scripts stay unchanged. The symbol's top layer is black
#: and vanishes on dark surfaces, so the on-dark variant lifts the dark tones (like Orkexa). With 0.6 the black layer
#: becomes a mid grey with about 3:1 contrast on near-black, and the three layers keep their order.
PRODUCT_CONFIG: derive_branding.Product = {"tile": (255, 255, 255), "lift": 0.6, "name": "Tabrion"}
THEME: social_preview.Theme = {
    "top": (30, 32, 37),
    "bottom": (11, 12, 14),
    "glow": (150, 158, 172),
    "strength": 0.12,
    "text": (205, 208, 214),
}

#: Derived files kept in this folder; the docs serve the same bytes (tests/unit/test_branding.py).
KEPT = (
    "tabrion-logo.png",
    "tabrion-logo-on-dark.png",
    "tabrion-mark-on-dark-128.png",
    "favicon.ico",
    "favicon-32.png",
    "apple-touch-icon.png",
)

#: One-line caption on the GitHub social preview.
SOCIAL_CAPTION = "Small, secure database foundations for SQLite and PostgreSQL"


def _grey(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB")).astype(np.float64)


def _clean(rgb: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    out[rgb.min(axis=2) >= BACKGROUND] = 255.0
    return out


def _solid(rgb: np.ndarray) -> np.ndarray:
    """Pixels inside a flat area of the artwork: not paper, and a 3 x 3 neighbourhood of nearly one grey value."""
    value = rgb.mean(axis=2)
    lo, hi = value.copy(), value.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.roll(np.roll(value, dy, 0), dx, 1)
            lo, hi = np.minimum(lo, shifted), np.maximum(hi, shifted)
    return (value < SOLID_MAX) & (hi - lo < 6.0)


def _nearest_solid_colour(rgb: np.ndarray, solid: np.ndarray, steps: int = 40) -> np.ndarray:
    """For every pixel, the colour of the nearest solid pixel (grown ring by ring, averaged per ring)."""
    colour = np.where(solid[..., None], rgb, 0.0)
    known = solid.astype(np.float64)
    for _ in range(steps):
        todo = known == 0
        if not todo.any():
            break
        acc = np.zeros_like(colour)
        cnt = np.zeros_like(known)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    acc += np.roll(np.roll(colour * known[..., None], dy, 0), dx, 1)
                    cnt += np.roll(np.roll(known, dy, 0), dx, 1)
        fill = todo & (cnt > 0)
        colour[fill] = acc[fill] / cnt[fill][:, None]
        known[fill] = 1.0
    return colour


#: Grey-value bands of the solid areas: the black layer and the wordmark, the dark grey and the light grey layer.
LAYER_BANDS = ((0.0, 40.0), (40.0, 150.0), (150.0, SOLID_MAX))


def _layer_colours(rgb: np.ndarray, solid: np.ndarray) -> np.ndarray:
    """Solid pixels set to their layer's median colour, so the paper and chroma noise of the raster drops out."""
    value = rgb.mean(axis=2)
    out = rgb.copy()
    for low, high in LAYER_BANDS:
        layer = solid & (value >= low) & (value < high)
        if layer.any():
            out[layer] = np.median(rgb[layer], axis=0)
    return out


#: Width (px, in the upscaled lockup) of the anti-aliased edge between artwork and paper.
EDGE = 4


def _near(mask: np.ndarray, radius: int) -> np.ndarray:
    """``mask`` grown by ``radius`` pixels (square neighbourhood)."""
    grown = mask.copy()
    for _ in range(radius):
        step = grown.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                step |= np.roll(np.roll(grown, dy, 0), dx, 1)
        grown = step
    return grown


def knock_out(rgb: np.ndarray, wordmark_x: int) -> np.ndarray:
    """RGBA from artwork on white: alpha reproduces each pixel's grey over white, colour from its own shape.

    Every pixel takes the colour of the nearest solid area (the median colour of that layer), and the alpha that
    turns that colour over white into the pixel's own grey value. Shapes and anti-aliasing come from the supplied
    pixels; only the colour noise inside each layer is dropped.
    """
    value = rgb.mean(axis=2)
    solid = _solid(rgb)
    base = _nearest_solid_colour(_layer_colours(rgb, solid), solid)
    base_value = base.mean(axis=2)
    # Wordmark: pure black with alpha; its solid grey (about 5) counts as fully opaque.
    base[:, wordmark_x:] = 0.0
    word = value[:, wordmark_x:]
    base_value[:, wordmark_x:] = np.median(word[solid[:, wordmark_x:]])
    alpha = np.clip((BACKGROUND - value) / np.maximum(BACKGROUND - base_value, 1.0), 0.0, 1.0)
    # Away from the paper there is no anti-aliasing: grey noise inside a layer must not turn into transparency.
    alpha[~_near(value >= BACKGROUND, EDGE)] = 1.0
    out = np.dstack([base, alpha * 255.0])
    out[alpha == 0] = 0.0
    return np.rint(out).astype(np.uint8)


def _gap_column(rgb: np.ndarray) -> int:
    """Middle of the widest paper-only column run: the gap between symbol and wordmark."""
    ink = (rgb.min(axis=2) < BACKGROUND).any(axis=0)
    cols = np.where(ink)[0]
    gaps = np.diff(cols)
    i = int(np.argmax(gaps))
    return int(cols[i] + cols[i + 1]) // 2


def prepare_masters(out: Path) -> None:
    """Write ``tabrion-logo-transparent.png`` and ``tabrion-logo-white.png`` (2048 x 640) into ``out``."""
    supplied = Image.open(SUPPLIED).convert("RGB")
    crop = _clean(_grey(supplied.crop(TOP_LOCKUP)))
    height = round(crop.shape[0] * MASTER_WIDTH / crop.shape[1])
    big = Image.fromarray(crop.astype(np.uint8), "RGB").resize((MASTER_WIDTH, height), Image.Resampling.LANCZOS)
    big_rgb = _clean(_grey(big))
    rgba = Image.fromarray(knock_out(big_rgb, _gap_column(big_rgb)), "RGBA")
    rgba = rgba.crop(rgba.getbbox())
    canvas = Image.new("RGBA", (2048, 640), (0, 0, 0, 0))
    canvas.alpha_composite(rgba, ((2048 - rgba.width) // 2, (640 - rgba.height) // 2))
    canvas.save(out / "tabrion-logo-transparent.png", optimize=True)
    derive_branding.flatten(canvas).save(out / "tabrion-logo-white.png", optimize=True)


def studio_master(path: Path) -> None:
    """The "Tabrion STUDIO" lockup of the supplied master, RGB on clean white, at the supplied resolution."""
    supplied = Image.open(SUPPLIED).convert("RGB")
    crop = _clean(_grey(supplied.crop(STUDIO_LOCKUP)))
    Image.fromarray(crop.astype(np.uint8), "RGB").save(path, optimize=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--studio", type=Path, help="also write the Studio lockup master to this file")
    args = parser.parse_args(argv)
    derive_branding.PRODUCTS[PRODUCT] = PRODUCT_CONFIG
    social_preview.THEMES[PRODUCT] = THEME

    prepare_masters(HERE)
    if derive_branding.main(["--product", PRODUCT, "--masters", str(HERE), "--check"]):
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        derived = ["--product", PRODUCT, "--masters", str(HERE), "--out", str(out), "--sets", "web,favicons"]
        if derive_branding.main(derived):
            return 1
        for name in KEPT:
            shutil.copyfile(out / name, HERE / name)

    # Social preview: the shared 1280 x 640 template (social_preview.py, the same in every product repository).
    social_preview.compose(PRODUCT, HERE, SOCIAL_CAPTION, None).save(HERE / "social-preview.png", optimize=True)

    DOCS.mkdir(parents=True, exist_ok=True)
    for name in KEPT:
        shutil.copyfile(HERE / name, DOCS / name)
    if args.studio:
        studio_master(args.studio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
