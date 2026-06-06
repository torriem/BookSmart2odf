# BookSmart2odf

Converts Blurb **BookSmart** photo books (`.book` files) into OpenDocument
documents that you can open and edit in LibreOffice — either **ODT**
(LibreOffice Writer) or **ODG** (LibreOffice Draw).

Blurb's BookSmart application is long discontinued. This project lets you recover
the contents of your old books, including full text, styles, images, decorative text-box
borders, and the cover, into an open, editable format.

It works two ways:

- a **command-line converter** (`booksmart2odf.py`) that writes an `.odt`/`.odg`
  file, and
- a **LibreOffice import-filter extension** that opens `.book` files directly
  from LibreOffice with File ▸ Open (see [below](#libreoffice-import-filter-open-book-files-directly)).

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

## LibreOffice import filter (open `.book` files directly)

As an alternative to the command-line converter, this project includes a
LibreOffice **import-filter extension** that lets you open a `.book` file
straight from LibreOffice with **File ▸ Open** — no separate conversion step.

Build the extension and install it (with LibreOffice closed):

```
./oxt/build.sh                        # produces oxt/booksmart-import.oxt
unopkg add oxt/booksmart-import.oxt
```

Then open a `.book` file in LibreOffice.

- By default the book opens in **Draw (ODG)**. To open it in **Writer (ODT)**
  instead, either open it from within a Writer window, or pick *BookSmart Book
  (Writer)* in the File ▸ Open file-type dropdown. (Opening from a Draw window
  likewise uses the Draw filter.)
- Only the **body pages** are imported.  The cover spread is not but you can use the
  command-line tool with `--cover` for that.
- Decorative **borders are not rendered** by the extension as it has no reliable
  way to find your BookSmart3 install (if you even have one) for the encrypted `.bev` ornament assets.
  Use the command-line `-b` option if you need borders.
- There is no `.book` *export* filter, so **File ▸ Save** offers *Save As* to a
  native format.  You can't accidentally overwrite the original `.book`.

Unlike the command-line converter, the extension runs inside LibreOffice's own
bundled Python and needs **none** of the Python modules listed under
[Requirements](#requirements): images are handled through LibreOffice's UNO
graphics API.

To update an already-installed copy, **remove then add** it (with LibreOffice
fully closed) rather than `unopkg add -f`, so LibreOffice rebuilds its filter
cache:

```
unopkg remove org.booksmart2odf.import
unopkg add oxt/booksmart-import.oxt
```

## Caveats

This script aims for a faithful reproduction, but it is not perfect:

- **Typography and positioning may not be exactly right.** Fonts, spacing, and
  the precise placement of elements can differ from BookSmart's rendering.
- **Image zoom and cropping are reproduced as set in BookSmart,** but depending
  on the image this is not always exact.

Expect to do some **manual editing** in LibreOffice afterward to get a polished,
printable document.

## Requirements

These apply to the **command-line converter**. The LibreOffice import-filter
extension needs none of them because it uses LibreOffice's bundled Python and 
UNO API to do the work.

Python modules:

- `ezodf`
- `lxml`
- `PIL` (Pillow) — used to normalize image DPI so LibreOffice sizes and
  crops images correctly
- `pycryptodome` / `Crypto` — only needed for decrypting border ornaments
  (the `-b` option)

There may be other incidental dependencies.
