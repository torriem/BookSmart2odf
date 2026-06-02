# pybooksmart

Converts Blurb **BookSmart** photo books (`.book` files) into OpenDocument
documents that you can open and edit in LibreOffice — either **ODT**
(LibreOffice Writer) or **ODG** (LibreOffice Draw).

Blurb's BookSmart application is long discontinued. This script lets you recover
the contents of your old books — text, styles, images, and decorative text-box
borders — into an open, editable format.

Only tested with the author's own photo books.

## Usage

```
./book2odt.py [options] <book_file>
```

(The script is still named `book2odt.py` even though it can now produce ODG as
well.)

Point it at the `.book` file inside your BookSmart data folder, e.g.:

```
./book2odt.py "~/BookSmartData/my book/my book.book"
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

Run `./book2odt.py --help` for the authoritative list.

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

- **The book's cover is not converted yet.**
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
