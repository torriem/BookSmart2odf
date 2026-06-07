# Image zooming and cropping in BookSmart, and how we reproduce it

This document explains how Blurb **BookSmart** stores the pan / zoom / crop of an
image inside a `.book` file, how the parser (`bookxml.py`) turns that into a set
of crop measurements, and how each output path renders it:

- the **command-line ODF generator** (`odfbuild.py`, writing ODF XML directly), and
- the **LibreOffice UNO variant** (`unobuild.py`, driving a live LibreOffice
  document through the UNO API).

---

## 1. How BookSmart describes an image

In BookSmart an image lives in a fixed rectangular **box** on the page. The user
never changes the box by panning/zooming; instead the *picture inside* the box
is scaled and slid around, and the box acts as a viewport that clips it. This is
the classic "fill the frame, then pan/zoom within it" model.

### The `.book` XML

Each image is an `ImageContent` element (parsed in
`bookxml.py:_read_images`). The pieces that matter:

| Source | Attribute | Meaning | Units |
| --- | --- | --- | --- |
| `ImageContent` | `content` | path to the picture file under `library/` | — |
| `ImageContent` | `re` | the **box**: `x, y, width, height` on the page | points |
| `ImageContent` | `rxt` | extra x-shift applied on even pages only (gutter/margin) | points |
| nested transform | `x`, `y` | **pan**: offset of the picture's top-left relative to the box | points |
| nested transform | `zoom` | **zoom** as a percentage (e.g. `100`, `150`) | percent |
| nested transform | `vflip`, `hflip` | vertical / horizontal mirror | `true`/`false` |

These are read onto an `ImageBox` (`bookxml.py:102`):

- `box_x, box_y, width, height` — the on-page viewport rectangle (`re`).
- `x, y` — the pan offset, in points, of the picture inside that box.
- `zoom` — stored as a fraction (`zoom / 100`).
- `vflip, hflip`.

Key conventions:

- **`zoom = 1.0` (100%) means "scale the picture so it just fills the box."**
  Zoom is relative to that fit, not to the picture's native pixel size.
- A **negative** pan (`x < 0` or `y < 0`) means the picture has been slid left /
  up, so its left / top is off the edge of the box — that part is clipped.
- If, after scaling and panning, the picture extends past the right / bottom of
  the box, that part is clipped too.

So the box is a window onto a scaled, panned picture, and anything outside the
window is cropped away.

---

## 2. Turning that into crop amounts: `ImageBox.calculate_crop()`

`bookxml.py:calculate_crop()` is the shared heart of all three paths. It
converts BookSmart's pan/zoom into four **crop insets** (`crop_left`,
`crop_right`, `crop_top`, `crop_bottom`) expressed as a fraction of the picture
that must be trimmed off each edge. (Internally they are computed in pixels and
then divided by `self.dpi`, so the meaning of the final numbers depends on what
`dpi` is set to — see §3 and §4.)

The steps:

1. **Find the fit scale (`pixperpt`).** At 100% zoom the picture is scaled so its
   *constraining* dimension exactly fills the box. Comparing the picture's
   aspect ratio with the box's aspect ratio tells us whether width or height is
   the constraining side; `pixperpt` is then `picture_pixels / box_points` along
   that side. (The cascade of `if` branches in `calculate_crop` is just the four
   aspect-ratio cases.)

2. **Apply zoom.** `clip_pixperpt = pixperpt / zoom`. Higher zoom → fewer
   picture-pixels per box-point → the picture is effectively larger relative to
   the box, so more of it falls outside and gets cropped.

3. **Compute the displayed picture size in points** from `clip_pixperpt`, then
   walk the four edges:
   - `x < 0` → crop `|x|` worth off the **left**; clamp `x` to 0.
   - `y < 0` → crop `|y|` worth off the **top**; clamp `y` to 0.
   - picture runs past the box's right edge → crop the overflow off the
     **right**.
   - picture runs past the box's bottom edge → crop the overflow off the
     **bottom**.
   Each overflow is converted from points to pixels via `clip_pixperpt`.

4. **Normalise to inches** by dividing each pixel crop by the image DPI
   (`self.dpi`), storing `crop_top/right/bottom/left`.

Note the side effect: `calculate_crop()` **clamps `self.x` / `self.y` to 0**
once it has turned a negative pan into a left/top crop. After the call, `x`/`y`
are the *non-negative* on-page offset of the (now clipped) picture within the
box. Both renderers rely on this.

### Why DPI matters

The crop maths starts in pixels, so the picture's stored resolution (DPI) is
needed to convert to a physical inch crop that ODF understands. If a file has a
missing or nonsensical DPI, LibreOffice mis-sizes the image and the crop is
applied at the wrong scale. The two output paths handle this completely
differently — that is the main thing that separates them.

---

## 3. Command-line path (`odfbuild.py`) — DPI normalization + ODF clip

The CLI generator writes ODF XML by hand and leans on **real image DPI**.

### Preparing the image (`odfbuild.py:prepare_images`)

Before emitting anything, each `ImageBox` is run through one of:

- `fix_dpi(...)` — if the picture's DPI is not already 300/600, its resolution
  **metadata is rewritten to 300 dpi** (via Pillow, or exiftool if requested).
  Depending on options this happens in a temp copy, a side-copy next to the
  original (`.300dpi.<fmt>`), or in place. This is necessary because LibreOffice
  sizes/crops embedded images using their declared DPI, and odd DPI values make
  the clip wrong (`bookxml.py:fix_dpi`).
- `crop_file()` — the destructive alternative (`-c/--crop`): physically crop the
  pixels with Pillow at 300 dpi and embed the already-cropped picture, so no ODF
  clip is needed at all. The original file on disk is never modified; a temp
  file is produced.

Then `calculate_crop()` runs with a real DPI, producing inch-based crop insets.

### Emitting the image (`odfbuild.py:build_image`)

The crop is expressed as the ODF **`fo:clip`** graphic property — a CSS-style
`rect(top, right, bottom, left)` in inches — on a `style:style` /
`style:graphic-properties` attached to the frame:

```
fo:clip = rect(crop_top in, crop_right in, crop_bottom in, crop_left in)
```

Mirroring becomes the ODF **`style:mirror`** property (`horizontal`,
`vertical`, or both).

Placement differs by output format:

- **ODT (Writer, `flatten=False`):** a *nested* structure — an outer box frame
  → `draw:text-box` → an inner subframe → the `draw:image`. The subframe is
  positioned at `(x, y)` (the clamped pan offset) inside the box, and the
  `fo:clip` trims the rest. This nesting is how Writer emulates BookSmart's
  pan/zoom/crop within a fixed viewport.
- **ODG (Draw, `flatten=True`):** one absolutely-positioned `draw:frame`
  carrying the same clip/mirror style, placed directly on the page at
  `(box_x + x, box_y + y)`. Draw doesn't render the nested-frame trick, so the
  frame is sized to the box and the clip does the work.

**Summary:** the CLI path makes the *image's own DPI* correct (or pre-crops the
pixels), then describes the crop declaratively in ODF XML with `fo:clip`.

---

## 4. UNO path (`unobuild.py`) — DPI-free, via `GraphicCrop`

The LibreOffice extension runs inside LibreOffice's bundled Python and has **no
Pillow / exiftool**. It never rewrites image metadata on disk and never
re-encodes pixels. Instead it works entirely from what LibreOffice itself
reports about the loaded graphic, and applies the crop through the UNO
`com.sun.star.text.GraphicCrop` property.

### Probing and loading

- `make_uno_prober` replaces `bookxml.probe_image` with a
  `GraphicProvider`-backed prober that returns the picture's **pixel** size and
  a **dummy `(72, 72)` DPI** — the real source DPI deliberately never enters the
  math (`unobuild.py:91`).
- `load_graphic` loads the picture by URL (so LibreOffice embeds the original
  encoded stream, no bloat) and reads its **natural size** `Size100thMM` and
  pixel size `SizePixel` (`unobuild.py:147`).

### The no-DPI problem and the in-memory JFIF patch

Some BookSmart JPEGs carry no resolution at all, so LibreOffice reports
`Size100thMM == 0` and there is no concrete physical size to crop against. This
was the original reason the CLI path normalized DPI to 300 on disk.

The UNO path solves it **in memory, losslessly** (`unobuild.py:_patch_jpeg_dpi`):

- if the JPEG already has a JFIF APP0 segment, patch its density fields in place
  (5 bytes) to a square 300 dpi;
- if it has no JFIF APP0 (e.g. an EXIF-only JPEG), splice in a minimal 18-byte
  JFIF APP0 right after the SOI marker.

LibreOffice honours the JFIF density, so after reloading from the patched byte
stream the graphic has a concrete, square-DPI `Size100thMM`. **No pixels are
re-encoded and the original file on disk is never touched.**

### Computing the crop: `graphic_crop` (DPI-free)

Rather than feed a real DPI into `calculate_crop`, the UNO path makes the crop a
**pure pixel fraction** (`unobuild.py:graphic_crop`):

1. set `ib.dpi = (pixel_width, pixel_height)` — i.e. tell `calculate_crop` the
   "DPI" *is* the pixel count, so its final pixels-÷-dpi step yields crop values
   already expressed as a **fraction of the picture (0–1)**;
2. call `ib.calculate_crop()` (same shared maths, same `x`/`y` clamping);
3. scale those fractions by LibreOffice's own natural size — `Size100thMM`, or a
   96-dpi fallback computed from the pixel size if it is still zero — to get a
   `GraphicCrop` in 1/100 mm:

```
crop.Left   = crop_left   * natural_width_100thmm
crop.Right  = crop_right  * natural_width_100thmm
crop.Top    = crop_top    * natural_height_100thmm
crop.Bottom = crop_bottom * natural_height_100thmm
```

Because the crop is derived as a fraction of whatever LibreOffice thinks the
picture's natural size is, it stays correct regardless of the source DPI — which
is why this path can ignore DPI normalization entirely.

### Placement and mirroring per backend

Both backends size the on-page image to the box (`width × height`) and position
it at `(box_x + x, box_y + y)` using the clamped pan offset, then attach the
`GraphicCrop`:

- **`DrawBackend.image` (ODG):** creates a `drawing.GraphicObjectShape`, sets
  `Graphic`, `Size`, `Position`, and `GraphicCrop`. A `GraphicObjectShape` has
  no mirror property, so flips are applied with `_apply_flip`, which negates the
  appropriate scale term of the shape's `Transformation` matrix while
  translating to preserve the bounding box (`unobuild.py:262`, `:554`).
- **`WriterBackend.image` (ODT):** creates a `text.TextGraphicObject`, anchors
  it to the page, sets `GraphicCrop`, and uses the native mirror properties
  `HoriMirroredOnEvenPages` / `HoriMirroredOnOddPages` (for `hflip`) and
  `VertMirrored` (for `vflip`) (`unobuild.py:752`).

**Summary:** the UNO path never relies on the file's DPI. It loads the graphic,
makes the natural size concrete with a lossless in-memory JFIF patch when
needed, derives the crop as a fraction of that natural size, and applies it via
`GraphicCrop` — with mirroring done by a transform-matrix flip in Draw and by
native mirror properties in Writer.

---

## 5. Side-by-side

| | CLI (`odfbuild.py`) | UNO (`unobuild.py`) |
| --- | --- | --- |
| Runs in | system Python (Pillow / exiftool) | LibreOffice bundled Python (no deps) |
| DPI handling | **normalizes** image DPI to 300 (metadata or pixel re-crop), may write temp/side-copy files | **ignores** source DPI; lossless in-memory JFIF patch only when natural size is 0 |
| Crop maths | `calculate_crop()` with real DPI → inch crops | `calculate_crop()` with `dpi = pixel size` → fractional crops |
| Crop applied as | ODF `fo:clip` rect (inches) in graphic style | UNO `GraphicCrop` (1/100 mm) on the graphic |
| Mirroring | ODF `style:mirror` | Draw: `Transformation` flip · Writer: `*Mirrored*` props |
| Touches original files? | only with `--crop`/in-place options (otherwise temp/side copies) | never |
| Re-encodes pixels? | yes for `--crop`; DPI fix re-encodes JPEG near-losslessly | no, ever |

The shared truth in both paths is `ImageBox.calculate_crop()`: BookSmart's
"viewport over a scaled, panned picture" is always reduced to four edge crops
plus a placement offset. Everything else is just how each output format prefers
to be told about that crop — and how each path makes the picture's size concrete
enough to compute it.
