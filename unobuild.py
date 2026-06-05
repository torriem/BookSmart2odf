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
import xml.etree.ElementTree as ET
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

def _patch_jpeg_dpi(raw, dpi=300):
    """Return DPI-stamped JPEG bytes, or None if ``raw`` is not a JPEG.

    Metadata-only, no pixel re-encode.  Two cases:

      * a JFIF APP0 already present -> patch its density in place (5 bytes).
      * otherwise (e.g. an EXIF-only JPEG, which has no JFIF APP0) -> splice an
        18-byte JFIF APP0 right after SOI.  LO honours the JFIF density over
        EXIF, so Size100thMM becomes a concrete, readable square-DPI natural
        size we can crop against.

    The input buffer is only read.
    """
    if raw[0:2] != b'\xff\xd8':
        return None
    if raw[2:4] == b'\xff\xe0' and raw[6:11] == b'JFIF\x00':
        data = bytearray(raw)
        data[13] = 1                              # units = dots/inch
        data[14:16] = dpi.to_bytes(2, 'big')      # Xdensity
        data[16:18] = dpi.to_bytes(2, 'big')      # Ydensity
        return bytes(data)
    # JPEG with no JFIF APP0: splice a minimal one in after the SOI marker.
    app0 = (b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x01'
            + dpi.to_bytes(2, 'big') + dpi.to_bytes(2, 'big') + b'\x00\x00')
    return raw[0:2] + app0 + raw[2:]


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
        patched = _patch_jpeg_dpi(raw)
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


def set_page_background(doc, page, color):
    """Set a drawing-page solid background fill on ``page``.

    This is the faithful equivalent of the CLI's drawing-page-properties
    ``draw:fill``/``draw:fill-color`` (ODG has no Writer-style page styles).
    ``com.sun.star.drawing.Background`` is the instantiable fill bag; assigned
    to the page's ``Background`` property it round-trips to the same ODF.
    """
    bg = doc.createInstance("com.sun.star.drawing.Background")
    bg.setPropertyValue(
        "FillStyle", uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"))
    bg.setPropertyValue("FillColor", color_to_int(color))
    page.Background = bg


def add_bg_rect(doc, page, width, height, color, x=0, y=0):
    """Solid background rectangle, for per-part cover backgrounds (a cover page
    holds several differently-coloured parts, so those can't use the single
    per-page background fill)."""
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


def add_text_box(doc, page, tb, para_styles, span_styles, page_no,
                 x_offset=0, valign_override=None, pad_top=0, pad_bottom=0):
    """A BookSmart text box -> a Draw TextShape with styled paragraphs.

    ``x_offset`` shifts the box right (used to lay cover parts side by side).
    ``valign_override`` (a TextVerticalAdjust value) forces vertical alignment,
    e.g. centred spine text.  ``pad_top``/``pad_bottom`` (points) inset the text
    so it clears top/bottom border ornaments -- applied geometrically (shrink the
    box) rather than via TextUpper/LowerDistance, which Draw does not reliably
    honour for text placement on a TextShape.
    """
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    if tb.rotation in (90, 270):
        # A rotated TextShape lays its text out in the UNROTATED size, then
        # rotates the result -- so for quarter turns the layout box must be
        # swapped (text flows along the box's long axis) and the shape placed so
        # its centre stays on the box centre (Draw rotates about the centre).
        w100, h100 = pt(tb.height), pt(tb.width)
        cx = pt(tb.x + x_offset + tb.width / 2.0)
        cy = pt(tb.y + tb.height / 2.0)
        shape.Size = Size(w100, h100)
        shape.Position = Point(cx - w100 // 2, cy - h100 // 2)
    else:
        # inset top/bottom for border ornaments by shrinking the box itself
        shape.Size = Size(pt(tb.width), pt(tb.height - pad_top - pad_bottom))
        shape.Position = Point(pt(tb.x + x_offset), pt(tb.y + pad_top))
    page.add(shape)

    _set(shape, TextAutoGrowHeight=False, TextAutoGrowWidth=False,
         TextLeftDistance=0, TextRightDistance=0,
         TextUpperDistance=0, TextLowerDistance=0)
    valign = valign_override if valign_override is not None \
        else TEXT_VADJUST.get(tb.valign)
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
                # BookSmart spans carry literal newlines as paragraph
                # terminators.  In ODF those are insignificant whitespace, but
                # insertString treats them as line breaks -- strip them so the
                # real line structure comes only from the paragraph list.
                clean = (s.text or '').replace('\r', '').replace('\n', '')
                text.insertString(cursor, clean, False)
    return shape


def _apply_flip(shape, x, y, w, h, hflip, vflip):
    """Mirror a shape in place via its Transformation matrix.

    GraphicObjectShape has no plain mirror property; flipping is a negative
    scale.  The bounding box is preserved by translating the negated axis.
    Nested UNO structs returned by attribute access are copies, so each Line is
    mutated locally and reassigned (else the change is silently lost).
    """
    if not (hflip or vflip):
        return
    t = shape.Transformation
    if hflip:
        l1 = t.Line1
        l1.Column1 = -l1.Column1   # negate x-scale
        l1.Column3 = x + w         # keep the bounding box
        t.Line1 = l1
    if vflip:
        l2 = t.Line2
        l2.Column2 = -l2.Column2   # negate y-scale
        l2.Column3 = y + h
        t.Line2 = l2
    shape.Transformation = t


def add_image(doc, page, smgr, ctx, ib, x_offset=0):
    """A BookSmart image box -> a GraphicObjectShape, sized + cropped + mirrored."""
    graphic, mm, px = load_graphic(smgr, ctx, ib.filename)

    # crop as a pixel fraction: setting ib.dpi to the pixel dims makes
    # calculate_crop() emit crop_* directly as fractions of width/height.
    ib.dpi = (px.Width or 1, px.Height or 1)
    ib.calculate_crop()

    sw, sh = pt(ib.width), pt(ib.height)
    sx, sy = pt(ib.box_x + ib.x + x_offset), pt(ib.box_y + ib.y)
    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    page.add(shape)
    shape.Graphic = graphic
    shape.Size = Size(sw, sh)
    shape.Position = Point(sx, sy)

    nat_w = mm.Width if mm.Width else int(px.Width / 96.0 * 2540)
    nat_h = mm.Height if mm.Height else int(px.Height / 96.0 * 2540)
    crop = uno.createUnoStruct("com.sun.star.text.GraphicCrop")
    crop.Left = int(ib.crop_left * nat_w)
    crop.Right = int(ib.crop_right * nat_w)
    crop.Top = int(ib.crop_top * nat_h)
    crop.Bottom = int(ib.crop_bottom * nat_h)
    _set(shape, GraphicCrop=crop)
    _apply_flip(shape, sx, sy, sw, sh, ib.hflip, ib.vflip)
    return shape


# --------------------------------------------------------------------------
# decorative text-box borders (ornament SVGs from the theme library)
# --------------------------------------------------------------------------
#
# A BookSmart text-box border places an ornament image above the text (top
# edge) and below it (bottom edge).  Mirrors odfborder's ODG path: each
# ornament is an absolutely-positioned shape centred on the text box at the
# top/bottom edge, and the text is inset by the ornament height.  The .bev
# assets are DES-encrypted SVGs under <booksmart_dir>/resources/themes/library.
#
# NOTE (Phase 3): bev.decrypt_bev pulls in pycryptodome (DES) and lxml, which a
# stock LibreOffice bundled Python lacks -- the .oxt will need a pure-Python DES
# and the et-based helpers below instead.  Imported lazily so the backend loads
# (and the non-border paths run) without those packages.

def _strip_unit(value):
    """SVG length like '45.848px' or '28' -> float (BookSmart treats units as pt)."""
    value = value.strip()
    for unit in ('px', 'pt', 'in', 'cm', 'mm'):
        if value.endswith(unit):
            value = value[:-len(unit)]
            break
    return float(value)


def _svg_dims(svg_bytes):
    """Return (width, height) in points of an SVG (root width/height or viewBox)."""
    root = ET.fromstring(svg_bytes)
    w, h = root.get('width'), root.get('height')
    if w is not None and h is not None:
        return _strip_unit(w), _strip_unit(h)
    vb = root.get('viewBox')
    if vb:
        parts = vb.replace(',', ' ').split()
        return float(parts[2]), float(parts[3])
    raise ValueError('SVG has no width/height or viewBox')


def _resolve_edges(border):
    """(top_spec, bot_spec) for a Border; each is (image_stem, mirrored) or None.

    Reimplements odfborder.resolve_edges.  Only top/bot edges with mirrorEdge
    0/OPPOSITE have ever been seen; left/right and MIRROR_ALL are unsupported.
    """
    top = (border.edges['top'], False) if 'top' in border.edges else None
    bot = (border.edges['bot'], False) if 'bot' in border.edges else None
    if border.mirror_edge == bookxml.Border.MIRROR_OPPOSITE:
        if top and not bot:
            bot = (border.edges['top'], True)
        elif bot and not top:
            top = (border.edges['bot'], True)
    elif border.mirror_edge == bookxml.Border.MIRROR_ALL:
        print('warning: border mirrorEdge=MIRROR_ALL not supported, '
              'drawing declared edges only')
    if 'left' in border.edges or 'right' in border.edges:
        print('warning: left/right border edges not supported, skipping')
    return top, bot


def _bev_path(stem, booksmart_dir):
    return os.path.join(booksmart_dir, 'resources', 'themes', 'library',
                        stem + '.bev')


def _edge_size(spec, booksmart_dir):
    """(width, height) pt of an ornament, or None if the .bev is missing."""
    import bev
    path = _bev_path(spec[0], booksmart_dir)
    if not os.path.exists(path):
        return None
    return _svg_dims(bev.decrypt_bev(path))


def border_pads(tb, booksmart_dir):
    """Return (top_spec, bot_spec, pad_top, pad_bottom) for a text box.

    The pads (ornament heights, in points) inset the text so it starts below a
    top ornament and stops above a bottom one.
    """
    if not (tb.border and booksmart_dir):
        return None, None, 0, 0
    top_spec, bot_spec = _resolve_edges(tb.border)
    pad_top = pad_bottom = 0
    if top_spec:
        size = _edge_size(top_spec, booksmart_dir)
        if size:
            pad_top = size[1]
    if bot_spec:
        size = _edge_size(bot_spec, booksmart_dir)
        if size:
            pad_bottom = size[1]
    return top_spec, bot_spec, pad_top, pad_bottom


def add_border_ornaments(doc, page, smgr, ctx, tb, top_spec, bot_spec,
                         booksmart_dir, x_offset=0):
    """Place the top/bottom border ornament shapes for a text box."""
    import bev
    gp = smgr.createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", ctx)
    for spec, is_top in ((top_spec, True), (bot_spec, False)):
        if not spec:
            continue
        stem, mirrored = spec
        path = _bev_path(stem, booksmart_dir)
        if not os.path.exists(path):
            print('warning: border image %s not found, skipping' % path)
            continue
        svg = bev.decrypt_bev(path)
        w, h = _svg_dims(svg)

        stream = smgr.createInstanceWithContext(
            "com.sun.star.io.SequenceInputStream", ctx)
        stream.initialize((uno.ByteSequence(svg),))
        graphic = gp.queryGraphic((_pv("InputStream", stream),))

        shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
        page.add(shape)
        shape.Graphic = graphic
        sw, sh = pt(w), pt(h)
        sx = pt(tb.x + tb.width / 2.0 - w / 2.0 + x_offset)
        sy = pt(tb.y) if is_top else pt(tb.y + tb.height - h)
        shape.Size = Size(sw, sh)
        shape.Position = Point(sx, sy)
        if mirrored:
            # reflect onto the opposite edge (top<->bottom)
            _apply_flip(shape, sx, sy, sw, sh, False, True)


def add_text_box_bordered(doc, page, smgr, ctx, tb, para_styles, span_styles,
                          page_no, booksmart_dir=None, x_offset=0,
                          valign_override=None):
    """Add a text box plus any decorative border ornaments around it."""
    top_spec, bot_spec, pad_top, pad_bottom = border_pads(tb, booksmart_dir)
    add_text_box(doc, page, tb, para_styles, span_styles, page_no,
                 x_offset=x_offset, valign_override=valign_override,
                 pad_top=pad_top, pad_bottom=pad_bottom)
    if top_spec or bot_spec:
        add_border_ornaments(doc, page, smgr, ctx, tb, top_spec, bot_spec,
                             booksmart_dir, x_offset)


# --------------------------------------------------------------------------
# page driver
# --------------------------------------------------------------------------

def inject_draw(doc, bs, smgr, ctx, page_limit=None, booksmart_dir=None):
    """Build the whole book body into ``doc`` (a DrawingDocument model).

    ``page_limit`` (optional) builds only the first N pages, for quick tests.
    ``booksmart_dir`` enables decorative text-box borders (ornament .bev assets).
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

        # page background via the drawing-page fill (matches the CLI)
        pstyle = page_styles.get(bs.page_info[page_id]['page_style'])
        if pstyle is not None and pstyle['bgcolor'] != '#ffffff':
            set_page_background(doc, page, pstyle['bgcolor'])

        # text boxes (with borders), then images on top (matches the .odg z-order)
        for tb in bs.text_boxes[page_id]:
            add_text_box_bordered(doc, page, smgr, ctx, tb, para_styles,
                                  span_styles, page_no,
                                  booksmart_dir=booksmart_dir)
        for ib in bs.images[page_id]:
            add_image(doc, page, smgr, ctx, ib)

    return doc


# print-wrap order of cover parts, left to right (mirrors booksmart2odf)
COVER_PRINT_ORDER = ['Back Flap', 'Back Cover', 'Spine', 'Front Cover',
                     'Front Flap']


def cover_spread(bs, no_flaps=False):
    """Return (ordered_parts, total_width, height) for the print-wrap spread.

    ``no_flaps`` drops the inner flap parts (wrapped hardcover / softcover).
    Mirrors booksmart2odf.cover_spread (reimplemented here so the backend stays
    free of the ezodf/lxml import).
    """
    parts = bs.cover
    if no_flaps:
        parts = [p for p in parts if 'Flap' not in p.title]

    def order(part):
        try:
            return COVER_PRINT_ORDER.index(part.title)
        except ValueError:
            return len(COVER_PRINT_ORDER)
    parts = sorted(parts, key=order)
    total_width = sum(p.width for p in parts)
    height = max(p.height for p in parts)
    return parts, total_width, height


def inject_cover(doc, bs, smgr, ctx, no_flaps=False, booksmart_dir=None):
    """Build the cover spread into ``doc`` as a single Draw page.

    Mirrors the ODG path of booksmart2odf.process_cover: one page sized to the
    whole print-wrap spread, parts laid left to right, each part stacking its
    background, then images, then text (so cover text sits above photos).
    ``booksmart_dir`` enables decorative text-box borders.
    """
    if not bs.cover:
        raise ValueError("This book has no cover to convert.")

    para_styles = {p['name']: p for p in bs.get_paragraph_styles()}
    span_styles = {s['name']: s for s in bs.get_span_styles()}

    parts, total_width, height = cover_spread(bs, no_flaps)

    page = doc.DrawPages.getByIndex(0)
    page.Width = pt(total_width)
    page.Height = pt(height)
    _set(page, BorderLeft=0, BorderRight=0, BorderTop=0, BorderBottom=0)

    x_off = 0
    for part in parts:
        # per-part background (a cover page mixes several part colours, so this
        # is a rectangle, not the single per-page Background fill)
        add_bg_rect(doc, page, part.width, part.height, part.bgcolor, x=x_off)

        for ib in part.images:
            add_image(doc, page, smgr, ctx, ib, x_offset=x_off)

        for tb in part.text_boxes:
            valign_override = None
            if tb.rotation in (90, 270):
                # rotated (spine) text: span the full part width, centre it
                tb.x = 0
                tb.width = part.width
                valign_override = VC
            add_text_box_bordered(doc, page, smgr, ctx, tb, para_styles,
                                  span_styles, 0, booksmart_dir=booksmart_dir,
                                  x_offset=x_off,
                                  valign_override=valign_override)

        x_off += part.width

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
    ap.add_argument("--cover", action="store_true",
                    help="build the cover spread instead of the book body")
    ap.add_argument("--no-flaps", action="store_true",
                    help="with --cover, omit the inner flaps from the spread")
    ap.add_argument("-b", "--booksmart-dir",
                    help="BookSmart3 program dir, to render decorative "
                         "text-box borders (ornament .bev assets)")
    args = ap.parse_args()

    ctx, smgr, desktop = _connect(args.port)

    # install the Pillow-free prober before parsing
    bookxml.probe_image = make_uno_prober(smgr, ctx)
    bs = bookxml.BookXML(args.book_file)
    print("Parsed: %s  (%dx%d pt, %d pages)" % (
        bs.info.get('booktitle', ''), bs.width, bs.height, len(bs.pages)))

    doc = desktop.loadComponentFromURL(
        "private:factory/sdraw", "_blank", 0, ())
    if args.cover:
        inject_cover(doc, bs, smgr, ctx, no_flaps=args.no_flaps,
                     booksmart_dir=args.booksmart_dir)
    else:
        inject_draw(doc, bs, smgr, ctx, page_limit=args.pages,
                    booksmart_dir=args.booksmart_dir)

    if args.output:
        out = args.output
    elif args.cover:
        out = os.path.splitext(args.book_file)[0] + " cover.odg"
    else:
        out = os.path.splitext(args.book_file)[0] + ".odg"
    doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(out)),
                   (_pv("FilterName", "draw8"),))
    doc.close(False)
    print("Wrote %s" % out)


if __name__ == "__main__":
    main()
