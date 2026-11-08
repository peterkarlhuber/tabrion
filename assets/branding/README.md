# Tabrion brand assets

`tabrion-logo-master-2026-11-08.png` is the official Tabrion logo master as supplied by Peter Karlhuber (1254 × 1254
px, RGB on white): the "Tabrion" lockup at the top, the "Tabrion STUDIO" lockup at the bottom. It is kept byte for
byte (`tests/unit/test_branding.py` checks its sha256). Every other image here is derived from it by `build.py` and
must not be edited by hand.

**License:** the logos and every image in this folder and in `docs/assets/branding/` are © Peter Karlhuber and are
**not** covered by the repository's MIT license; see [TRADEMARKS.md](../../TRADEMARKS.md). The scripts are MIT.

The committed master is the supplied file with its metadata chunks removed (a C2PA content-credentials manifest,
`caBX`, and an `eXIf` chunk with the dimensions). The image data is untouched: the test pins the sha256 of the
decompressed pixel data of the supplied file. The supplied file itself stays unchanged in the planning folder.

| File | Size | Use |
|------|------|-----|
| `tabrion-logo-master-2026-11-08.png` | 1254 × 1254 | master as supplied, do not modify |
| `tabrion-logo-transparent.png` | 2048 × 640 | brand kit master prepared from the top lockup: transparent background, black wordmark |
| `tabrion-logo-white.png` | 2048 × 640 | the same flattened on white (the brand kit checks that both agree) |
| `tabrion-logo.png` | 960 wide | lockup with black wordmark for light surfaces: README hero, docs landing page (light scheme) |
| `tabrion-logo-on-dark.png` | 960 wide | lockup with white wordmark and lifted symbol for dark surfaces: README hero (dark mode), docs landing page (dark scheme) |
| `tabrion-mark-on-dark-128.png` | 128 × 128 | symbol for dark surfaces: docs header logo (the header bar is dark slate in both schemes) |
| `favicon.ico` | 16, 32, 48 | browser favicon (symbol on a rounded white tile, readable on light and dark tabs) |
| `favicon-32.png` | 32 × 32 | PNG favicon (docs site) |
| `apple-touch-icon.png` | 180 × 180 | home-screen icon (opaque white tile) |
| `social-preview.png` | 1280 × 640 | GitHub social preview: the on-dark lockup on the shared dark brand template (uploaded manually in the repository settings) |

The README shows the lockups through absolute `raw.githubusercontent.com` URLs on `master`, because it is also the
PyPI description, where relative image paths break. The URLs work once this folder is on `master`.

The "Tabrion STUDIO" lockup is cropped from the same master by `build.py --studio <file>` and kept in the planning
folder as `tabrion-studio-logo-master-2026-11-08.png` for the tabrion-studio repository.

## Rules

* Nothing is redrawn. `build.py` prepares the brand kit's transparent master from the supplied pixels: it crops the
  top lockup (which also leaves out a faint ghost of old letters right of the wordmark), sets paper white and the
  ghost (grey values 247-255) to white, upscales with Lanczos and knocks out the white: each pixel keeps the shape
  and anti-aliasing of the supplied artwork as alpha, and takes the median colour of its layer (black, slate grey,
  light grey; the wordmark pure black), which drops the colour noise of the raster.
* `derive_branding.py` and `social_preview.py` are the brand kit scripts, vendored unchanged from
  `nodestro-studio/scripts/` (the test checks their sha256). `build.py` registers Tabrion in their product and theme
  tables at run time instead of editing them.
* The symbol's top layer is black and disappears on dark surfaces, so the on-dark variants lift the dark tones
  (`lift` 0.6, hue and saturation kept, as for Orkexa): the three layers stay in order and the top layer reaches
  about 3:1 contrast on near-black. The light variants are unchanged.
* Light surfaces use `tabrion-logo.png`, dark surfaces `tabrion-logo-on-dark.png`; the switch follows the surface's
  own theme mechanism (`<picture>` with `prefers-color-scheme` in the README, `#only-light` / `#only-dark` in MkDocs
  Material). No CSS filters or boxes behind the logo.
* Below about 40 px height, use the symbol instead of the full lockup.
* There is no vector (SVG) version; an official vector master would have to come from the logo's designer.

## Regenerating

```bash
uv run --with pillow==12.3.0 --with numpy==2.5.3 python assets/branding/build.py
```

`build.py` prepares the two brand kit masters, checks them with `derive_branding.py --check`, derives the web and
favicon sets, keeps the files listed above, renders `social-preview.png` with `social_preview.py` and copies the web
assets into `docs/assets/branding/`, because MkDocs only serves files below `docs/`. `tests/unit/test_branding.py`
checks that the copies match. The committed files were produced with Pillow 12.3.0 and NumPy 2.5.3 (the pinned versions above); other versions
may write different bytes, so commit only the files you meant to change.
