# BookSmart2odf

Converts Blurb **BookSmart** photo books (`.book` files) into OpenDocument
documents that you can open and edit in LibreOffice — either **ODT**
(LibreOffice Writer) or **ODG** (LibreOffice Draw).

Blurb's BookSmart application is long discontinued. This script lets you recover
the contents of your old books, including full text, styles, images, decorative text-box
borders, and the cover, into an open, editable format.

Note I have only tested this with my own photo books.  You may have to do manual editing to ensure everything is correct.

## Usage

```
./booksmart2odf.py [options] <book_file>
```

Point it at the `.book` file inside your BookSmart data folder, e.g.:

```
./booksmart2odf.py "~/BookSmartData/my book/my book.book"
```

### Options

- `-o, --output OUTPUT` — where to write the result. Defaults to the book's name
  with the format's extension, alongside the `.book` file.
- `-f, --format {odt,odg}` — output format. `odt` (the default) produces a
  LibreOffice Writer document; `odg` produces a LibreOffice Draw document.
- `-c, --crop` — physically crop the images stored inside the document instead
  of just zooming/soft-cropping them. The original image files are left alone.
- `-b, --booksmart-dir BOOKSMART_DIR` — path to the installed **BookSmart3**
  program directory (the one containing `resources/themes/library`). This is
  required to render decorative text-box borders, because the border ornament
  images live there encrypted as `.bev` files and must be decrypted at
  conversion time. If omitted, borders are simply skipped.
- `--cover` — convert the book's **cover** instead of the body (see below).
- `--no-flaps` — with `--cover`, omit the inner flaps from the spread (see
  below).

Run `./booksmart2odf.py --help` for the authoritative list.

## Cover

The cover should be laid out in its own file. Pass `--cover` to convert the cover instead of the book body:

```
./booksmart2odf.py --cover "~/BookSmartData/my book/my book.book"
```

This writes `<book> cover.<ext>` next to the `.book` file (override with `-o`),
and honours `-f/--format` like the body. All cover parts are placed on a single
combined spread page in print-wrap order, left to right:

```
Back Flap | Back Cover | Spine | Front Cover | Front Flap
```

Each part keeps its own background and dimensions, and the page is sized to the
whole spread. Spine text is rotated to read down the spine.

For a **wrapped hardcover or a softcover**, add `--no-flaps` to leave the inner
flaps off the spread; the resulting cover's dimensions are reduced accordingly
(soft covers have no flaps to begin with, so the flag is a no-op there).

## ODT vs. ODG

The two formats reproduce the book in different ways, and each has trade-offs.

**ODT (LibreOffice Writer)** — the book becomes a flowing text document, one
page per book page (via page breaks). Text reflows as you edit it. Border
ornaments flow *with* the text: the top ornament sits above the text and the
bottom ornament moves down as the text grows. Best when you intend to edit the
wording and want the layout to adjust.

**ODG (LibreOffice Draw)** — each book page becomes a real drawing page, and
every text box and image is an absolutely-positioned shape. This matches
BookSmart's fixed, page-layout nature more directly and tends to be better for
precise placement and print fidelity. The cost is that nothing reflows: text
does not move between pages, and a border ornament is pinned to the top/bottom
edge of its text box rather than flowing with the text.

Pick ODT if you want to keep editing the text, ODG if you want a faithful
fixed layout.

## Caveats

This script aims for a faithful reproduction, but it is not perfect:

- **Typography and positioning may not be exactly right.** Fonts, spacing, and
  the precise placement of elements can differ from BookSmart's rendering.
- **Image zoom and cropping are reproduced as set in BookSmart,** but depending
  on the image this is not always exact.

Expect to do some **manual editing** in LibreOffice afterward to get a polished,
printable document.

## Requirements

Python modules:

- `ezodf`
- `lxml`
- `PIL` (Pillow)
- `pycryptodome` / `Crypto` — only needed for decrypting border ornaments
  (the `-b` option)

External tools:

- `exiftool` — used to normalize image DPI so cropping comes out right.

(There may be other incidental dependencies.)
