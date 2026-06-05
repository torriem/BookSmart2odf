"""UNO Draw backend: inject a parsed BookSmart book into a live LibreOffice
Drawing document via the UNO API, instead of writing ODF XML.

This mirrors the .odg path in ``booksmart2odf.process_odg_pages`` /
``odfbuild.build_*`` but drives a ``com.sun.star.drawing.DrawingDocument``
model directly.  It is the backend the import filter will call; a small
``__main__`` lets it run standalone against a headless ``soffice`` socket so we
can iterate without packaging an .oxt.

Design notes (see import_filter_plan.md, Phase 2):
  * BookSmart geometry is in points; the Draw API is 1/100 mm -> ``pt()``.
  * No Pillow/exiftool: ``bookxml.probe_image`` is replaced with a
    GraphicProvider-based prober (pixel size only).
  * Image sizing is explicit via ``shape.Size``; cropping via ``GraphicCrop``
    computed as a pixel fraction of LO's own natural size.  Files with no
    embedded DPI get a lossless in-memory JFIF density patch so Size100thMM is
    readable -- the originals on disk are never touched.

Deferred for a later pass: decorative text-box borders, the cover spread,
image mirroring (vflip/hflip), and live page-number fields.
"""

import os
import sys
import uno
from com.sun.star.awt import Size, Point
from com.sun.star.beans import PropertyValue
from com.sun.star.awt.FontWeight import BOLD, NORMAL
from com.sun.star.awt.FontSlant import ITALIC, NONE as SLANT_NONE
from com.sun.star.awt.FontUnderline import SINGLE, NONE as UL_NONE
from com.sun.star.style.ParagraphAdjust import LEFT, RIGHT, CENTER, BLOCK
from com.sun.star.drawing.TextVerticalAdjust import (
    TOP, CENTER as VC, BOTTOM)

import bookxml

PT_TO_MM100 = 2540.0 / 72.0


def pt(points):
    """BookSmart points -> Draw 1/100 mm."""
    return int(round(points * PT_TO_MM100))


def color_to_int(hexstr):
    """'#rrggbb' (or 'rrggbb') -> 0xRRGGBB int."""
    if not hexstr:
        return 0
    return int(hexstr.lstrip('#')[-6:], 16)


def _pv(name, value):
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p


# BookSmart ParagraphStyle.ALIGN enum -> UNO ParagraphAdjust
PARA_ADJUST = {0: LEFT, 1: CENTER, 2: RIGHT, 3: BLOCK}
# BookSmart text-box 'va' -> UNO TextVerticalAdjust (absent/0 == top)
TEXT_VADJUST = {3: VC, 4: BOTTOM}

# MIME -> short format string (matches the old Pillow prober's image.format.lower())
_MIME_FORMAT = {
    'image/jpeg': 'jpeg', 'image/png': 'png', 'image/gif': 'gif',
    'image/tiff': 'tiff', 'image/bmp': 'bmp',
}


# --------------------------------------------------------------------------
# image introspection seam (replaces Pillow)
# --------------------------------------------------------------------------

def make_uno_prober(smgr, ctx):
    """Return a ``probe_image`` replacement backed by GraphicProvider.

    Returns ``(format, (w, h) pixels, (xdpi, ydpi))``.  The DPI is a dummy:
    the Draw backend sizes images explicitly and derives the crop from a pixel
    fraction, so the source DPI never enters the math.  Raises FileNotFoundError
    for a missing file so ImageBox's ``.original`` fallback still works.
    """
    gp = smgr.createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", ctx)

    def probe(filepath):
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)
        desc = gp.queryGraphicDescriptor(
            (_pv("URL", uno.systemPathToFileUrl(filepath)),))
        px = desc.SizePixel
        fmt = _MIME_FORMAT.get(desc.MimeType, '')
        if not fmt:
            fmt = os.path.splitext(filepath)[1].lstrip('.').lower() or 'jpeg'
        return fmt, (px.Width, px.Height), (72, 72)

    return probe


# --------------------------------------------------------------------------
# graphic loading (with lossless in-memory DPI patch for no-DPI files)
# --------------------------------------------------------------------------

def _patch_jfif_dpi(raw, dpi=300):
    """Return JFIF-DPI-patched bytes, or None if not a patchable JFIF JPEG.

    Metadata-only: sets the APP0 density units to dpi and X/Y density to
    ``dpi``.  No pixel re-encode, no length change.  The input buffer is only
    read.
    """
    if raw[0:2] != b'\xff\xd8':
        return None
    if raw[2:4] == b'\xff\xe0' and raw[6:11] == b'JFIF\x00':
        data = bytearray(raw)
        data[13] = 1
        data[14:16] = dpi.to_bytes(2, 'big')
        data[16:18] = dpi.to_bytes(2, 'big')
        return bytes(data)
    return None


def load_graphic(smgr, ctx, filepath):
    """Load an XGraphic, ensuring Size100thMM is concrete.

    Loads via URL (so LO embeds the original encoded stream, no bloat).  If the
    file carries no resolution (Size100thMM == 0) and is a patchable JPEG, patch
    the density in memory and reload from a byte stream so the natural size
    becomes readable.  Returns ``(graphic, size100thMM, sizePixel)``.
    """
    gp = smgr.createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", ctx)
    graphic = gp.queryGraphic((_pv("URL", uno.systemPathToFileUrl(filepath)),))
    mm = graphic.Size100thMM
    if mm.Width == 0 or mm.Height == 0:
        with open(filepath, 'rb') as fh:
            raw = fh.read()
        patched = _patch_jfif_dpi(raw)
        if patched is not None:
            stream = smgr.createInstanceWithContext(
                "com.sun.star.io.SequenceInputStream", ctx)
            stream.initialize((uno.ByteSequence(patched),))
            graphic = gp.queryGraphic((_pv("InputStream", stream),))
            mm = graphic.Size100thMM
    return graphic, mm, graphic.SizePixel


# --------------------------------------------------------------------------
# shape builders
# --------------------------------------------------------------------------

def _set(obj, **props):
    """setPropertyValue for each, ignoring ones the shape doesn't support."""
    for name, value in props.items():
        try:
            obj.setPropertyValue(name, value)
        except Exception:
            pass


def add_bg_rect(doc, page, width, height, color, x=0, y=0):
    """Full-part solid background rectangle (page bg / cover-part bg)."""
    rect = doc.createInstance("com.sun.star.drawing.RectangleShape")
    rect.Size = Size(pt(width), pt(height))
    rect.Position = Point(pt(x), pt(y))
    page.add(rect)
    _set(rect, FillStyle=uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"),
         FillColor=color_to_int(color),
         LineStyle=uno.Enum("com.sun.star.drawing.LineStyle", "NONE"))
    return rect


def _effective_char_props(para_style, span_style):
    """Merge a paragraph style with a span style (span overrides non-None)."""
    eff = {}
    if para_style is not None:
        eff.update(font=para_style['font'], size=para_style['size'],
                   color=para_style['color'], bold=para_style['bold'],
                   italic=para_style['italic'], underline=para_style['underline'])
    if span_style is not None:
        for k in ('font', 'size', 'color', 'bold', 'italic', 'underline'):
            v = span_style[k]
            if v is not None:
                eff[k] = v
    return eff


def _apply_char_props(cursor, eff):
    if eff.get('font'):
        _set(cursor, CharFontName=eff['font'])
    if eff.get('size') is not None:
        _set(cursor, CharHeight=float(eff['size']))
    if eff.get('color'):
        _set(cursor, CharColor=color_to_int(eff['color']))
    _set(cursor, CharWeight=BOLD if eff.get('bold') else NORMAL)
    _set(cursor, CharPosture=ITALIC if eff.get('italic') else SLANT_NONE)
    _set(cursor, CharUnderline=SINGLE if eff.get('underline') else UL_NONE)


def add_text_box(doc, page, tb, para_styles, span_styles, page_no):
    """A BookSmart text box -> a Draw TextShape with styled paragraphs."""
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    shape.Size = Size(pt(tb.width), pt(tb.height))
    shape.Position = Point(pt(tb.x), pt(tb.y))
    page.add(shape)

    _set(shape, TextAutoGrowHeight=False, TextAutoGrowWidth=False,
         TextLeftDistance=0, TextRightDistance=0,
         TextUpperDistance=0, TextLowerDistance=0)
    valign = TEXT_VADJUST.get(tb.valign)
    if valign is not None:
        _set(shape, TextVerticalAdjust=valign)
    if tb.rotation:
        # UNO RotateAngle is 1/100 deg, counter-clockwise; BookSmart is clockwise
        _set(shape, RotateAngle=int((360 - tb.rotation) % 360) * 100)

    text = shape.Text
    text.setString("")
    cursor = text.createTextCursor()

    for pi, para in enumerate(tb.paragraphs):
        if pi > 0:
            text.insertControlCharacter(
                cursor,
                uno.getConstantByName(
                    "com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK"),
                False)
        ps = para_styles.get(para.style)
        if ps is not None:
            _set(cursor, ParaAdjust=PARA_ADJUST.get(ps['alignment'], LEFT))
            if ps['left_indent']:
                _set(cursor, ParaLeftMargin=pt(ps['left_indent']))

        for s in para.spans:
            if s.variable and not (s.text or '').strip():
                continue
            eff = _effective_char_props(ps, span_styles.get(s.style))
            _apply_char_props(cursor, eff)
            if s.variable == '$PageNumber':
                # TODO: live page-number field; static for now
                text.insertString(cursor, str(page_no + 1), False)
            else:
                text.insertString(cursor, s.text or '', False)
    return shape


def add_image(doc, page, smgr, ctx, ib, x_offset=0):
    """A BookSmart image box -> a GraphicObjectShape, sized + cropped."""
    graphic, mm, px = load_graphic(smgr, ctx, ib.filename)

    # crop as a pixel fraction: setting ib.dpi to the pixel dims makes
    # calculate_crop() emit crop_* directly as fractions of width/height.
    ib.dpi = (px.Width or 1, px.Height or 1)
    ib.calculate_crop()

    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    page.add(shape)
    shape.Graphic = graphic
    shape.Size = Size(pt(ib.width), pt(ib.height))
    shape.Position = Point(pt(ib.box_x + ib.x + x_offset), pt(ib.box_y + ib.y))

    nat_w = mm.Width if mm.Width else int(px.Width / 96.0 * 2540)
    nat_h = mm.Height if mm.Height else int(px.Height / 96.0 * 2540)
    crop = uno.createUnoStruct("com.sun.star.text.GraphicCrop")
    crop.Left = int(ib.crop_left * nat_w)
    crop.Right = int(ib.crop_right * nat_w)
    crop.Top = int(ib.crop_top * nat_h)
    crop.Bottom = int(ib.crop_bottom * nat_h)
    _set(shape, GraphicCrop=crop)
    # TODO: vflip/hflip mirroring (BookSmart transform), decorative borders
    return shape


# --------------------------------------------------------------------------
# page driver
# --------------------------------------------------------------------------

def inject_draw(doc, bs, smgr, ctx, page_limit=None):
    """Build the whole book body into ``doc`` (a DrawingDocument model).

    ``page_limit`` (optional) builds only the first N pages, for quick tests.
    """
    para_styles = {p['name']: p for p in bs.get_paragraph_styles()}
    span_styles = {s['name']: s for s in bs.get_span_styles()}
    page_styles = {p['name']: p for p in bs.get_page_styles()}

    pages = doc.DrawPages
    n = len(bs.pages) if page_limit is None else min(page_limit, len(bs.pages))

    for page_no in range(n):
        page_id = bs.pages[page_no]
        while pages.Count <= page_no:
            pages.insertNewByIndex(pages.Count)
        page = pages.getByIndex(page_no)
        page.Width = pt(bs.width)
        page.Height = pt(bs.height)
        _set(page, BorderLeft=0, BorderRight=0, BorderTop=0, BorderBottom=0)

        # page background (full-page rect at the bottom of the stack)
        pstyle = page_styles.get(bs.page_info[page_id]['page_style'])
        if pstyle is not None and pstyle['bgcolor'] != '#ffffff':
            add_bg_rect(doc, page, bs.width, bs.height, pstyle['bgcolor'])

        # text boxes, then images on top (matches the .odg z-order)
        for tb in bs.text_boxes[page_id]:
            add_text_box(doc, page, tb, para_styles, span_styles, page_no)
        for ib in bs.images[page_id]:
            add_image(doc, page, smgr, ctx, ib)

    return doc


# --------------------------------------------------------------------------
# standalone runner (headless soffice socket) -- not used by the filter
# --------------------------------------------------------------------------

def _connect(port, timeout=30.0):
    import time
    from com.sun.star.connection import NoConnectException
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = ("uno:socket,host=localhost,port=%d;urp;"
           "StarOffice.ComponentContext" % port)
    deadline = time.time() + timeout
    while True:
        try:
            ctx = resolver.resolve(url)
            break
        except NoConnectException:
            if time.time() > deadline:
                raise
            time.sleep(0.5)
    smgr = ctx.ServiceManager
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    return ctx, smgr, desktop


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Inject a .book into a live Draw "
                                 "document via UNO (standalone test driver).")
    ap.add_argument("book_file")
    ap.add_argument("-p", "--port", type=int, default=2002)
    ap.add_argument("-o", "--output", help="output .odg (default: <book>.odg)")
    ap.add_argument("--pages", type=int, help="only build the first N pages")
    args = ap.parse_args()

    ctx, smgr, desktop = _connect(args.port)

    # install the Pillow-free prober before parsing
    bookxml.probe_image = make_uno_prober(smgr, ctx)
    bs = bookxml.BookXML(args.book_file)
    print("Parsed: %s  (%dx%d pt, %d pages)" % (
        bs.info.get('booktitle', ''), bs.width, bs.height, len(bs.pages)))

    doc = desktop.loadComponentFromURL(
        "private:factory/sdraw", "_blank", 0, ())
    inject_draw(doc, bs, smgr, ctx, page_limit=args.pages)

    out = args.output or (os.path.splitext(args.book_file)[0] + ".odg")
    doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(out)),
                   (_pv("FilterName", "draw8"),))
    doc.close(False)
    print("Wrote %s" % out)


if __name__ == "__main__":
    main()
