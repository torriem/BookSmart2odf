# The BookSmart `.book` format and on-disk book layout

This is a reverse-engineered description of how Blurb **BookSmart** stores a
photo book: the directory layout on disk, the structure of the `.book` XML, how
text and its styling are encoded, how images are referenced and stored, and how
decorative text-box borders work. It reflects what this project's parser
(`bookxml.py`) actually relies on; it is not Blurb's official spec, and only
covers the parts we have needed.

Companion docs:

- `image_cropping.md` — how the pan/zoom/crop maths works.
- `BEV_FORMAT.md` — the encryption of the `.bev` ornament assets.

---

## 1. On-disk layout of a book

A BookSmart book is a **directory**, typically under a `BookSmartData` folder,
named after the book. Inside it:

```
my book/
├── my book.book          # the document: one big XML file
├── my book.backup        # a previous saved copy of the .book XML
├── content.xml           # app bookkeeping (not needed by us)
├── content2.xml
└── library/              # every picture used by the book, plus variants
    ├── <uuid>.original    # the full-resolution imported picture
    ├── <uuid>.zoom        # a large display copy
    ├── <uuid>.screen      # a screen-resolution copy
    ├── <uuid>.thumb       # a thumbnail
    └── booklogo_interior.screen.png   # a few named theme assets
```

### The `library/` folder

Each user photo is stored under a **UUID stem** with up to four JPEG variants:

| Suffix | Purpose | Typical size |
| --- | --- | --- |
| `.original` | the imported picture at full resolution (often EXIF JPEG) | largest |
| `.zoom` | a high-res display copy (JFIF JPEG) | large |
| `.screen` | a screen-resolution copy | small |
| `.thumb` | a thumbnail | tiny |

The book XML refers to a picture by its **bare UUID, with no extension**
(e.g. `content="ff6da51b-…"`). There is usually no file at that bare name, so
the parser appends `.original` to load the full-resolution variant
(`bookxml.py:_read_images`, which tries the bare name first and falls back to
`<uuid>.original`). The `.original` is the one we always want for output
fidelity; the other variants are BookSmart's own caches.

Note the `.original` variant is frequently an **EXIF-only JPEG with no JFIF APP0
segment and no usable resolution**, which is exactly why both output paths have
to deal with DPI (see `image_cropping.md`).

A handful of theme assets (page logos, etc.) live in `library/` under plain
names like `booklogo_interior.screen.png`.

---

## 2. Top-level `.book` XML structure

The `.book` file is a single XML document. The root is `<Book>`:

```xml
<Book id="my book" title="…" bookGuid="…" theme="10x8L_viewfinder"
      revision="30022" cover="…" width="693" height="594" …>
  <bookVar name="$BookTitle"  value="Italy 2002"/>
  <bookVar name="$Subtitle"   value="Photo Album"/>
  <bookVar name="$AuthorName" value="by Michael Torrie"/>
  …
  <TextStyleDefinition id="Heading 1" title="Black (16pt)"
                       font="Georgia" size="16" color="#ff000000"/>
  …
  <pagesList> … </pagesList>
  <bookObjects> … </bookObjects>
</Book>
```

Parsed by `BookXML.__init__` (`bookxml.py:607`):

- **`<Book>` attributes** — `width` / `height` are the page size in **points**
  (1/72 inch); `bookGuid` is the stable identifier the import filter sniffs for;
  `cover` points at the cover object.
- **`<bookVar>`** — document-wide variables. Names start with `$` (e.g.
  `$BookTitle`, `$AuthorName`, `$PageNumber`). Collected into `self.info`
  keyed by the lower-cased name without the `$`. These are the values that get
  substituted where a text span references a variable (see §3).
- **`<TextStyleDefinition>`** — named, reusable text styles (font, size, color,
  alignment, bold/italic/underline). Referenced by id from text boxes and from
  the per-character style "resolver". Colors are stored as `#AARRGGBB`; the
  parser trims the leading alpha byte to `#RRGGBB`.
- **`<pagesList>`** — the *ordered* list of page ids. This defines page order;
  the actual page content lives in `<bookObjects>`.
- **`<bookObjects>`** — a flat pool of every page, cover part, text box and
  image object, cross-referenced by id. This is the heart of the document.

### `<bookObjects>` and the parent/child model

Objects in `<bookObjects>` are **flat** and linked by id rather than nested:

- `<Page id="…">` — a body page. Carries `layout`, `partOfSpread`,
  `pagination`, and a `<BackgroundDefinition>` child.
- `<TextContent parentId="…">` — a text box belonging to the page (or cover
  part) whose id equals `parentId`.
- `<ImageContent parentId="…">` — an image box, likewise linked by `parentId`.
- `<HardCover>` / `<SoftCover>` and `<CoverPage>` — the cover (see §5).

So to assemble a page the parser walks `pagesList` for order, finds the matching
`Page` in `bookObjects`, then collects all `TextContent`/`ImageContent` whose
`parentId` is that page's id (`bookxml.py:read_pages`).

### Page background

Each `<Page>` (and each cover part) has a `<BackgroundDefinition>`:

```xml
<BackgroundDefinition id="solid01" color="#ffffffff"/>
```

Only the solid `color` is used (parsed into `PageStyle`, alpha trimmed to
`#RRGGBB`). Identical backgrounds are de-duplicated into a small set of page
styles.

### Geometry conventions

- All positions and sizes are in **points**.
- A box's rectangle is the `re` attribute: `x, y, width, height`.
- `rxt` is an x-shift that is **only applied on even pages** (a gutter/binding
  offset). The parser adds it to `x` when `pageno` is even and ignores it on odd
  pages.
- `cr` on a text box is a clockwise rotation in degrees (used for spine text).
- `va` on a text box is a vertical-alignment enum.

---

## 3. Text: `TextContent`, styles, and the embedded Java structure

A text box is a `<TextContent>` element:

```xml
<TextContent id="…" parentId="…" re="56,189,191,333" rxt="-1"
             ts="cap_l_9-12_s4" cr="0" va="…">
  <BorderDefinition …/>        <!-- optional decorative border -->
  <dm>…serialized Java…</dm>   <!-- the actual rich text -->
</TextContent>
```

Box-level attributes:

- `re` — the box rectangle (points).
- `rxt` — even-page x-shift (as above).
- `ts` — id of the default `TextStyleDefinition` for the box.
- `cr` — clockwise rotation in degrees (optional).
- `va` — vertical alignment enum (optional).

### The `<dm>` payload — Java `XMLDecoder` beans

The rich text itself is **not** native BookSmart XML. It is a **serialized Java
object graph** produced by `java.beans.XMLEncoder`, stored as escaped text
inside `<dm>`:

```xml
<java version="21.0.11" class="java.beans.XMLDecoder">
 <object class="java.util.LinkedList">
  <void method="add">
   <object class="java.util.HashMap">
    <void method="put"><string>resolver</string>
                       <string>cap_l_9-12_s4.chars</string></void>
    …
```

The parser re-parses this inner XML and walks it with `javaxml_to_python`
(`bookxml.py:515`), which understands a small subset of the Java bean encoding:

- `java.util.LinkedList` → Python `list`
- `java.util.HashMap` → Python `dict`
- `java.awt.Color` → an `(r, g, b, a)` tuple (with an optional reusable
  `color_id`)

The decoded top-level list alternates between:

1. a **paragraph dict** — starts a new paragraph. May carry `resolver` (a
   `<style>.chars` reference that overrides the box's default style),
   `Alignment`, `LeftIndent`, `LineSpacing`.
2. a **runs list** — the spans of that paragraph. Each span is an optional
   **style dict** followed by its **text string**. The style dict may set
   `size`, `family` (font), `foreground` (a `java.awt.Color` or a `color_id`
   reference into a color cache), `bold`, `italic`, `underline`, and `bsVar`.

`bsVar` marks a span whose text is a **document variable** (e.g. `$PageNumber`,
`$BookTitle`) — its value comes from the `<bookVar>` table, and `$PageNumber` in
particular is what the UNO Writer backend turns into a live page-number field.

The parser normalizes all of this into plain `TextBox` → `Paragraph` → `Span`
objects, building de-duplicated `ParagraphStyle` and `SpanStyle` tables
(`PS0`, `PS1`, … / `SS0`, `SS1`, …) so the output side can emit a compact set of
named styles.

### Colors

Colors appear as `#AARRGGBB` (leading alpha byte); the project consistently
trims the alpha and uses `#RRGGBB`. Inline `java.awt.Color` definitions can
carry a `color_id` so later spans reference the color by id instead of
repeating the RGBA — the parser caches these in `self._color_cache`.

---

## 4. Images: `ImageContent` and the transform

An image box is an `<ImageContent>` linked to its page by `parentId`:

```xml
<ImageContent id="…" parentId="…" re="0,0,734,613" rxt="-1"
              content="ff6da51b-…" fitPolicy="fitContainer">
  <transformation>
    <TransformEffect x="-95" y="0" angle="0" zoom="100.0"
                     hflip="false" vflip="false"/>
  </transformation>
</ImageContent>
```

- `content` — the **UUID stem** of the picture in `library/` (no extension; the
  parser loads `<uuid>.original`). An `ImageContent` with no `content` attribute
  is an **empty placeholder box** and is skipped.
- `re` — the image **box** (viewport) rectangle on the page, in points.
- `rxt` — even-page x-shift (as for text).
- `<transformation>/<TransformEffect>` — the pan/zoom/mirror of the picture
  *inside* the box:
  - `x`, `y` — pan offset in points (negative = picture slid left/up, so its
    edge is clipped by the box);
  - `zoom` — percentage, where **100% means "scale to fill the box"**;
  - `hflip`, `vflip` — mirror flags;
  - `angle` — present in the data but not currently used by the parser.

How those numbers become an actual crop is documented in detail in
`image_cropping.md`. In short: the box is a window onto a scaled, panned
picture, and `ImageBox.calculate_crop()` reduces the transform to four edge
crops plus a placement offset.

---

## 5. The cover

The cover is a separate object referenced by `Book/@cover` and stored as
`<HardCover>` or `<SoftCover>` in `bookObjects` (`bookxml.py:read_cover`):

- `<HardCover>` / `<SoftCover>` contains a `<pagesList>` of `<pages>` ids, each
  pointing at a `<CoverPage>` object.
- Each `<CoverPage>` is a **cover part** with its own size (`w`, `h` in points),
  its own `<BackgroundDefinition>`, and its own `TextContent` / `ImageContent`
  children (parsed exactly like a body page).
- Part `title` identifies the role: `Back Flap`, `Back Cover`, `Spine`,
  `Front Cover`, `Front Flap`. For a **soft cover**, parts whose title contains
  `Flap` are skipped (soft covers have no flaps).

For output the parts are laid out left-to-right in print-wrap order
(`Back Flap | Back Cover | Spine | Front Cover | Front Flap`) into one combined
spread; spine text is rotated to read down the spine. The LibreOffice import
filter deliberately does **not** import the cover (body pages only); the cover
path is for the CLI converter.

---

## 6. Decorative text-box borders

Some text boxes carry a `<BorderDefinition>` describing a decorative frame made
of **ornament images** placed along the box edges:

```xml
<BorderDefinition id="elegant_frame_03" icon="icon_elegant_frame_03.svg"
                  inset="77" color="#ff000000"
                  tileEdges="false" mirrorEdge="2">
  <edge location="top" image="elegant_frame_03_02.svg"/>
</BorderDefinition>
```

Parsed into a `Border` (`bookxml.py:46`):

- `inset` — how far the text is inset from the box edge (points).
- `color` — frame color (alpha trimmed).
- `tileEdges` — whether the ornament tiles along the edge.
- `mirrorEdge` — how a single declared edge is propagated to the others:
  - `0` MIRROR_OFF — draw only the declared edges;
  - `1` MIRROR_ALL — mirror/rotate the declared edge to all four sides
    (not supported by this project);
  - `2` MIRROR_OPPOSITE — mirror the declared edge to the opposite side
    (the common case: declare `top`, get a mirrored `bot` for free).
- `<edge location="top|bot|left|right" image="…svg"/>` — which ornament image
  sits on which edge. The parser keeps the **image stem** (the `.svg` extension
  stripped). Only `top`/`bot` edges with `mirrorEdge` 0 or `OPPOSITE` have been
  seen in practice; `left`/`right` and `MIRROR_ALL` are unsupported and warned
  about.

### Where the ornament images live (and why borders are optional)

The ornament images are **not** in the book's `library/` folder. They are theme
assets that ship with the **BookSmart3 program install**, under:

```
<booksmart_dir>/resources/themes/library/<stem>.bev
```

Each `.bev` is a **single DES-encrypted image** — the `*_frame_*` ornaments
decrypt to SVG, the larger `*_pat_*` patterns to PNG/JPEG. The key and full
details are in `BEV_FORMAT.md`. The `BorderDefinition` names the image as
`elegant_frame_03_02.svg`; on disk that is `elegant_frame_03_02.bev`, decrypted
at conversion time.

Because rendering a border requires:

1. a BookSmart3 installation to read the `.bev` assets from, and
2. DES decryption (pycryptodome in the CLI),

borders are **opt-in**:

- the **CLI** renders them only when given the BookSmart3 program directory via
  `-b/--booksmart-dir`; otherwise they are silently skipped;
- the **LibreOffice import filter** never renders them — an extension has no
  reliable way to locate a BookSmart3 install, and LibreOffice's bundled Python
  lacks a DES implementation, so `booksmart_dir` is left `None`.

### How a border is drawn

When assets are available, an ornament is placed as an absolutely-positioned
image centred on the text box at its top/bottom edge, and the text is inset by
the ornament's height so it starts below the top ornament and stops above the
bottom one (`unobuild.py:border_pads` and `_resolve_edges`; the CLI equivalent
is `odfborder.py`). The ornament's own dimensions come from the decrypted SVG's
`width`/`height` (or its `viewBox`).

---

## 7. Quick reference: element → parser

| XML | Meaning | Parsed by |
| --- | --- | --- |
| `<Book>` | root; page size, guid, cover ref | `BookXML.__init__` |
| `<bookVar>` | document variables (`$PageNumber`, …) | `BookXML.__init__` → `self.info` |
| `<TextStyleDefinition>` | named reusable text style | `read_book_styles` |
| `<pagesList>` | ordered page ids | `read_pages` |
| `<bookObjects>` | flat object pool | `read_pages` |
| `<Page>` + `<BackgroundDefinition>` | a body page + its bg color | `read_pages` / `PageStyle` |
| `<TextContent>` + `<dm>` | a text box + its Java-encoded rich text | `_read_text_boxes` / `javaxml_to_python` |
| `<ImageContent>` + `<TransformEffect>` | an image box + its pan/zoom/mirror | `_read_images` / `ImageBox` |
| `<BorderDefinition>`/`<edge>` | decorative ornament frame | `Border` |
| `<HardCover>`/`<SoftCover>`/`<CoverPage>` | the cover and its parts | `read_cover` / `CoverPart` |
| `library/<uuid>.original` | the actual picture for an `ImageContent` | `_read_images` |
| `<booksmart_dir>/…/library/<stem>.bev` | DES-encrypted border ornament | `bev.decrypt_bev` |
