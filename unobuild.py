"""UNO backend: inject a parsed BookSmart book into a live LibreOffice document
via the UNO API, instead of writing ODF XML.

Two backends share one driver: :class:`DrawBackend` builds a Drawing (ODG) model
and :class:`WriterBackend` a Text (ODT) model.  Everything format-neutral --
geometry/colour conversion, the image prober and loader, crop maths, character
styling and the paragraph/span text fill, border ornament resolution, the cover
spread -- lives at module level or on the :class:`Backend` base; each subclass
implements only the handful of operations that genuinely differ (document
factory, page setup, and creating/positioning the text/image/background shapes).

The import filter (future) picks the backend matching the model LibreOffice
hands it: a DrawingDocument -> DrawBackend, a TextDocument -> WriterBackend.  A
small ``__main__`` runs either standalone against a headless ``soffice`` socket
(``-f odg`` / ``-f odt``) so we can iterate without packaging an .oxt.

Design notes (see import_filter_plan.md):
  * BookSmart geometry is in points; the UNO API is 1/100 mm -> ``pt()``.
  * No Pillow/exiftool: ``bookxml.probe_image`` is replaced with a
    GraphicProvider-based prober (pixel size only).
  * Image sizing is explicit; cropping via ``GraphicCrop`` computed as a pixel
    fraction of LO's own natural size.  Files with no embedded DPI get a
    lossless in-memory JFIF density patch so Size100thMM is readable -- the
    originals on disk are never touched.
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
from com.sun.star.drawing.TextVerticalAdjust import TOP, CENTER as VC, BOTTOM

import bookxml

PT_TO_MM100 = 2540.0 / 72.0


def pt(points):
    """BookSmart points -> 1/100 mm."""
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


def _set(obj, **props):
    """setPropertyValue for each, ignoring ones the object doesn't support."""
    for name, value in props.items():
        try:
            obj.setPropertyValue(name, value)
        except Exception:
            pass


# BookSmart ParagraphStyle.ALIGN enum -> UNO ParagraphAdjust
PARA_ADJUST = {0: LEFT, 1: CENTER, 2: RIGHT, 3: BLOCK}
# BookSmart text-box 'va' -> UNO TextVerticalAdjust (absent/0 == top)
TEXT_VADJUST = {3: VC, 4: BOTTOM}

# MIME -> short format string (matches the old Pillow prober's image.format.lower())
_MIME_FORMAT = {
    'image/jpeg': 'jpeg', 'image/png': 'png', 'image/gif': 'gif',
    'image/tiff': 'tiff', 'image/bmp': 'bmp',
}

_PARA_BREAK = uno.getConstantByName(
    "com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK")


# --------------------------------------------------------------------------
# image introspection seam (replaces Pillow)
# --------------------------------------------------------------------------

def make_uno_prober(smgr, ctx):
    """Return a ``probe_image`` replacement backed by GraphicProvider.

    Returns ``(format, (w, h) pixels, (xdpi, ydpi))``.  The DPI is a dummy: the
    backends size images explicitly and derive the crop from a pixel fraction,
    so the source DPI never enters the math.  Raises FileNotFoundError for a
    missing file so ImageBox's ``.original`` fallback still works.
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


def graphic_crop(ib, mm, px):
    """Build a com.sun.star.text.GraphicCrop for an image box.

    The crop is a pure pixel fraction (DPI-free): setting ib.dpi to the pixel
    dims makes calculate_crop() emit crop_* directly as fractions, which we
    scale by LO's own natural size (Size100thMM, or a 96-dpi fallback).
    """
    ib.dpi = (px.Width or 1, px.Height or 1)
    ib.calculate_crop()
    nat_w = mm.Width if mm.Width else int(px.Width / 96.0 * 2540)
    nat_h = mm.Height if mm.Height else int(px.Height / 96.0 * 2540)
    crop = uno.createUnoStruct("com.sun.star.text.GraphicCrop")
    crop.Left = int(ib.crop_left * nat_w)
    crop.Right = int(ib.crop_right * nat_w)
    crop.Top = int(ib.crop_top * nat_h)
    crop.Bottom = int(ib.crop_bottom * nat_h)
    return crop


# --------------------------------------------------------------------------
# shared text styling / fill (works on any XText: Draw shape or Writer frame)
# --------------------------------------------------------------------------

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


def fill_text(xtext, tb, para_styles, span_styles, page_no,
              page_number_field=None):
    """Fill an XText (Draw TextShape.Text or a Writer frame's Text) with tb's
    styled paragraphs.  Container creation/positioning is the backend's job;
    this is the format-neutral content.

    ``page_number_field(xtext, cursor)`` inserts a live page-number field for a
    ``$PageNumber`` variable (Writer); when None a static number is written
    (Draw, where a live field is not reliably rendered).
    """
    xtext.setString("")
    cursor = xtext.createTextCursor()
    for pi, para in enumerate(tb.paragraphs):
        if pi > 0:
            xtext.insertControlCharacter(cursor, _PARA_BREAK, False)
        ps = para_styles.get(para.style)
        if ps is not None:
            _set(cursor, ParaAdjust=PARA_ADJUST.get(ps['alignment'], LEFT))
            if ps['left_indent']:
                _set(cursor, ParaLeftMargin=pt(ps['left_indent']))

        for s in para.spans:
            if s.variable and not (s.text or '').strip():
                continue
            _apply_char_props(cursor, _effective_char_props(
                ps, span_styles.get(s.style)))
            if s.variable == '$PageNumber':
                if page_number_field is not None:
                    page_number_field(xtext, cursor)
                else:
                    xtext.insertString(cursor, str(page_no + 1), False)
            else:
                # BookSmart spans carry literal newlines as paragraph
                # terminators.  In ODF those are insignificant whitespace, but
                # insertString treats them as line breaks -- strip them so the
                # real line structure comes only from the paragraph list.
                clean = (s.text or '').replace('\r', '').replace('\n', '')
                xtext.insertString(cursor, clean, False)


def _apply_flip(shape, x, y, w, h, hflip, vflip):
    """Mirror a Draw shape in place via its Transformation matrix.

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


# --------------------------------------------------------------------------
# decorative text-box borders (ornament SVGs from the theme library)
# --------------------------------------------------------------------------
#
# A BookSmart text-box border places an ornament image above the text (top
# edge) and below it (bottom edge).  Each ornament is an absolutely-positioned
# shape/frame centred on the text box at the top/bottom edge; the text is inset
# by the ornament height.  The .bev assets are DES-encrypted SVGs under
# <booksmart_dir>/resources/themes/library.
#
# NOTE (Phase 3): bev.decrypt_bev pulls in pycryptodome (DES), which a stock
# LibreOffice bundled Python lacks -- the .oxt would need a pure-Python DES.
# Imported lazily so the backend loads (and the non-border paths run) without
# it.

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
    """(width, height) pt of an ornament; None if no path or the .bev is missing.

    bev (DES/pycryptodome) is imported here, only once a BookSmart3 path is in
    play, so the backend runs without it when borders aren't used.
    """
    if not booksmart_dir:
        return None
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


# print-wrap order of cover parts, left to right (mirrors booksmart2odf)
COVER_PRINT_ORDER = ['Back Flap', 'Back Cover', 'Spine', 'Front Cover',
                     'Front Flap']


def cover_spread(bs, no_flaps=False):
    """Return (ordered_parts, total_width, height) for the print-wrap spread."""
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


# ==========================================================================
# backends
# ==========================================================================

class Backend:
    """Base: holds the doc/connection and the shared graphic helpers; subclasses
    implement the document-model-specific operations.

    Pages are created sequentially: begin_page() advances an internal physical
    page counter, so a document can be composed by building the cover page first
    and the body pages after.  Subclass contract (positions/sizes in points):
      factory / filter_name / default_ext  -- class attributes
      setup()                               -- one-time per-document setup
      begin_page(width, height, bgcolor='#ffffff', restart_number=False)
                                            -- start the next physical page
      bg_rect(x, y, w, h, color)            -- a solid rectangle (cover parts)
      text_box(tb, page_no, ...)            -- a styled text box (page_no is the
                                               book page number for $PageNumber)
      image(ib, x_offset=0)                 -- a sized/cropped/mirrored image
      ornament(graphic, w, h, x, y, mirror) -- a border ornament

    Placement always targets the current page (the last begun); ``restart_number``
    resets the page-number count to 1 (used on the first body page so book page
    numbers ignore the cover).
    """

    factory = None
    filter_name = None
    default_ext = None
    # Draw forces one page size for the whole document; Writer varies it per
    # page via page styles.  Only the latter can hold the spread-sized cover and
    # the body pages in one document.
    uniform_page_size = False

    def __init__(self, doc, smgr, ctx, booksmart_dir=None):
        self.doc = doc
        self.smgr = smgr
        self.ctx = ctx
        self.booksmart_dir = booksmart_dir
        self.para_styles = {}
        self.span_styles = {}
        self.page_styles = {}
        self._bs = None
        self._phys = -1

    def init_styles(self, bs):
        self.para_styles = {p['name']: p for p in bs.get_paragraph_styles()}
        self.span_styles = {s['name']: s for s in bs.get_span_styles()}
        self.page_styles = {p['name']: p for p in bs.get_page_styles()}

    def page_bgcolor(self, page_id):
        ps = self.page_styles.get(
            self._bs.page_info[page_id]['page_style']) if self._bs else None
        return ps['bgcolor'] if ps else '#ffffff'

    def svg_graphic(self, svg_bytes):
        gp = self.smgr.createInstanceWithContext(
            "com.sun.star.graphic.GraphicProvider", self.ctx)
        stream = self.smgr.createInstanceWithContext(
            "com.sun.star.io.SequenceInputStream", self.ctx)
        stream.initialize((uno.ByteSequence(svg_bytes),))
        return gp.queryGraphic((_pv("InputStream", stream),))

    def setup(self):
        """One-time per-document setup; subclasses extend."""
        self._phys = -1

    def _new_page(self):
        """Advance to the next physical page (0-based index) and return it."""
        self._phys += 1
        return self._phys


class DrawBackend(Backend):
    """Build a Drawing (ODG) model: one DrawPage per book page, shapes placed by
    absolute Position/Size."""

    factory = "private:factory/sdraw"
    filter_name = "draw8"
    default_ext = "odg"
    uniform_page_size = True

    def begin_page(self, width, height, bgcolor='#ffffff', restart_number=False):
        idx = self._new_page()
        pages = self.doc.DrawPages
        while pages.Count <= idx:
            pages.insertNewByIndex(pages.Count)
        page = pages.getByIndex(idx)
        page.Width = pt(width)
        page.Height = pt(height)
        _set(page, BorderLeft=0, BorderRight=0, BorderTop=0, BorderBottom=0)
        self._page = page
        if bgcolor and bgcolor != '#ffffff':
            # drawing-page solid fill (ODG has no Writer-style page styles);
            # com.sun.star.drawing.Background is the instantiable fill bag.
            bg = self.doc.createInstance("com.sun.star.drawing.Background")
            bg.setPropertyValue(
                "FillStyle", uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"))
            bg.setPropertyValue("FillColor", color_to_int(bgcolor))
            page.Background = bg
        # restart_number: $PageNumber is rendered statically in Draw, so the
        # restart is just the book number the driver passes to text_box.

    def bg_rect(self, x, y, width, height, color):
        rect = self.doc.createInstance("com.sun.star.drawing.RectangleShape")
        rect.Size = Size(pt(width), pt(height))
        rect.Position = Point(pt(x), pt(y))
        self._page.add(rect)
        _set(rect,
             FillStyle=uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"),
             FillColor=color_to_int(color),
             LineStyle=uno.Enum("com.sun.star.drawing.LineStyle", "NONE"))
        return rect

    def text_box(self, tb, page_no, x_offset=0, valign_override=None,
                 pad_top=0, pad_bottom=0):
        shape = self.doc.createInstance("com.sun.star.drawing.TextShape")
        if tb.rotation in (90, 270):
            # A rotated TextShape lays its text out in the UNROTATED size, then
            # rotates -- so for quarter turns swap the layout box (text flows
            # along the long axis) and place it centred on the box centre.
            w100, h100 = pt(tb.height), pt(tb.width)
            cx = pt(tb.x + x_offset + tb.width / 2.0)
            cy = pt(tb.y + tb.height / 2.0)
            shape.Size = Size(w100, h100)
            shape.Position = Point(cx - w100 // 2, cy - h100 // 2)
        else:
            # inset top/bottom for border ornaments by shrinking the box itself
            shape.Size = Size(pt(tb.width), pt(tb.height - pad_top - pad_bottom))
            shape.Position = Point(pt(tb.x + x_offset), pt(tb.y + pad_top))
        self._page.add(shape)

        _set(shape, TextAutoGrowHeight=False, TextAutoGrowWidth=False,
             TextLeftDistance=0, TextRightDistance=0,
             TextUpperDistance=0, TextLowerDistance=0)
        valign = valign_override if valign_override is not None \
            else TEXT_VADJUST.get(tb.valign)
        if valign is not None:
            _set(shape, TextVerticalAdjust=valign)
        if tb.rotation:
            # UNO RotateAngle is 1/100 deg counter-clockwise; BookSmart clockwise
            _set(shape, RotateAngle=int((360 - tb.rotation) % 360) * 100)

        fill_text(shape.Text, tb, self.para_styles, self.span_styles, page_no)
        return shape

    def image(self, ib, x_offset=0):
        graphic, mm, px = load_graphic(self.smgr, self.ctx, ib.filename)
        crop = graphic_crop(ib, mm, px)            # mutates ib.x/ib.y (clamps)
        sw, sh = pt(ib.width), pt(ib.height)
        sx, sy = pt(ib.box_x + ib.x + x_offset), pt(ib.box_y + ib.y)
        shape = self.doc.createInstance(
            "com.sun.star.drawing.GraphicObjectShape")
        self._page.add(shape)
        shape.Graphic = graphic
        shape.Size = Size(sw, sh)
        shape.Position = Point(sx, sy)
        _set(shape, GraphicCrop=crop)
        _apply_flip(shape, sx, sy, sw, sh, ib.hflip, ib.vflip)
        return shape

    def ornament(self, graphic, w, h, x, y, mirrored):
        shape = self.doc.createInstance(
            "com.sun.star.drawing.GraphicObjectShape")
        self._page.add(shape)
        shape.Graphic = graphic
        sw, sh = pt(w), pt(h)
        sx, sy = pt(x), pt(y)
        shape.Size = Size(sw, sh)
        shape.Position = Point(sx, sy)
        if mirrored:
            _apply_flip(shape, sx, sy, sw, sh, False, True)
        return shape


class WriterBackend(Backend):
    """Build a Text (ODT) model: boxes/images are frames anchored AT_PAGE at
    absolute positions; pages come from page-break paragraphs that switch to a
    per-background-colour page style."""

    factory = "private:factory/swriter"
    filter_name = "writer8"
    default_ext = "odt"

    _AT_PAGE = uno.Enum("com.sun.star.text.TextContentAnchorType", "AT_PAGE")
    _THROUGH = uno.Enum("com.sun.star.text.WrapTextMode", "THROUGH")
    _HORI_NONE = uno.getConstantByName("com.sun.star.text.HoriOrientation.NONE")
    _VERT_NONE = uno.getConstantByName("com.sun.star.text.VertOrientation.NONE")
    _PAGE_FRAME = uno.getConstantByName(
        "com.sun.star.text.RelOrientation.PAGE_FRAME")

    def _page_style_for(self, color, width, height):
        """Return the name of a page style with this background AND size,
        creating it on first use (zero margins, no header/footer).  The name
        encodes size too so cover (spread-sized) and body pages don't collide on
        one shared style."""
        name = "bs_%s_%dx%d" % (color.lstrip('#'),
                                int(round(width)), int(round(height)))
        if name not in self._styles_made:
            fams = self.doc.StyleFamilies.getByName("PageStyles")
            if fams.hasByName(name):
                ps = fams.getByName(name)
            else:
                ps = self.doc.createInstance("com.sun.star.style.PageStyle")
                fams.insertByName(name, ps)
            _set(ps, Width=pt(width), Height=pt(height),
                 LeftMargin=0, RightMargin=0, TopMargin=0, BottomMargin=0,
                 HeaderIsOn=False, FooterIsOn=False)
            if color != '#ffffff':
                _set(ps, BackColor=color_to_int(color), BackTransparent=False)
            self._styles_made.add(name)
        return name

    def setup(self):
        super().setup()
        self._styles_made = set()
        self._pageno_offset = 0   # added to $PageNumber fields (see begin_page)
        self._text = self.doc.Text
        self._cursor = self._text.createTextCursor()
        self._cursor.gotoStart(False)

    def begin_page(self, width, height, bgcolor='#ffffff', restart_number=False):
        idx = self._new_page()
        name = self._page_style_for(bgcolor, width, height)
        if idx == 0:
            _set(self._cursor, PageDescName=name)   # first page's style
        else:
            self._text.insertControlCharacter(self._cursor, _PARA_BREAK, False)
            _set(self._cursor, PageDescName=name)   # forces a new page + style
        if restart_number:
            # Restart the book's page-number count at 1 on this page.  Setting
            # the paragraph PageNumberOffset would do it but spuriously inserts a
            # phantom page in this LO build, so instead offset the $PageNumber
            # fields by -(pages before this one): physical page (_phys+1) +
            # offset == 1 here.
            self._pageno_offset = -self._phys

    def _anchor(self, content, x, y, w, h):
        """Insert a frame/graphic and pin it absolutely to the current page.

        AnchorType must be set AFTER insertion -- setting it before insert
        silently degrades to AT_CHARACTER.
        """
        self._text.insertTextContent(self._cursor, content, False)
        content.AnchorType = self._AT_PAGE
        _set(content,
             AnchorPageNo=self._phys + 1,
             HoriOrient=self._HORI_NONE, VertOrient=self._VERT_NONE,
             HoriOrientRelation=self._PAGE_FRAME,
             VertOrientRelation=self._PAGE_FRAME,
             HoriOrientPosition=pt(x), VertOrientPosition=pt(y),
             Width=pt(w), Height=pt(h),
             Surround=self._THROUGH)
        # Writer frames default to a visible border; Draw shapes don't -- strip it
        nb = uno.createUnoStruct("com.sun.star.table.BorderLine2")
        nb.LineWidth = 0
        nb.LineStyle = 0
        _set(content, LeftBorder=nb, RightBorder=nb, TopBorder=nb,
             BottomBorder=nb)

    def bg_rect(self, x, y, width, height, color):
        frame = self.doc.createInstance("com.sun.star.text.TextFrame")
        self._anchor(frame, x, y, width, height)
        _set(frame,
             FillStyle=uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"),
             FillColor=color_to_int(color), FillTransparence=0,
             FrameIsAutomaticHeight=False,
             SizeType=uno.getConstantByName("com.sun.star.text.SizeType.FIX"),
             BorderDistance=0, LeftBorderDistance=0, RightBorderDistance=0,
             TopBorderDistance=0, BottomBorderDistance=0)
        return frame

    def text_box(self, tb, page_no, x_offset=0, valign_override=None,
                 pad_top=0, pad_bottom=0):
        frame = self.doc.createInstance("com.sun.star.text.TextFrame")
        self._anchor(frame, tb.x + x_offset, tb.y + pad_top, tb.width,
                     tb.height - pad_top - pad_bottom)
        # See-through over images: a fully transparent fill (FillTransparence
        # =100, the CLI's draw:opacity="0%").  FillStyle=NONE alone still paints
        # the frame white in Writer.  Borderless, no padding, fixed height.
        _set(frame,
             FillStyle=uno.Enum("com.sun.star.drawing.FillStyle", "SOLID"),
             FillColor=0xFFFFFF, FillTransparence=100, BorderDistance=0,
             LeftBorderDistance=0, RightBorderDistance=0,
             TopBorderDistance=0, BottomBorderDistance=0,
             FrameIsAutomaticHeight=False,
             SizeType=uno.getConstantByName("com.sun.star.text.SizeType.FIX"))
        # TODO: rotated (spine) text and vertical alignment in Writer frames
        fill_text(frame.Text, tb, self.para_styles, self.span_styles, page_no,
                  page_number_field=self._page_number_field)
        return frame

    def _page_number_field(self, xtext, cursor):
        """Insert a live current-page-number field (arabic), offset so book
        numbering ignores any preceding cover page."""
        field = self.doc.createInstance("com.sun.star.text.TextField.PageNumber")
        _set(field,
             SubType=uno.Enum("com.sun.star.text.PageNumberType", "CURRENT"),
             NumberingType=4,  # com.sun.star.style.NumberingType.ARABIC
             Offset=self._pageno_offset)
        xtext.insertTextContent(cursor, field, False)

    def image(self, ib, x_offset=0):
        graphic, mm, px = load_graphic(self.smgr, self.ctx, ib.filename)
        crop = graphic_crop(ib, mm, px)            # mutates ib.x/ib.y (clamps)
        img = self.doc.createInstance("com.sun.star.text.TextGraphicObject")
        img.Graphic = graphic
        self._anchor(img, ib.box_x + ib.x + x_offset, ib.box_y + ib.y,
                     ib.width, ib.height)
        _set(img, GraphicCrop=crop)
        if ib.hflip:
            _set(img, HoriMirroredOnEvenPages=True, HoriMirroredOnOddPages=True)
        if ib.vflip:
            _set(img, VertMirrored=True)
        return img

    def ornament(self, graphic, w, h, x, y, mirrored):
        img = self.doc.createInstance("com.sun.star.text.TextGraphicObject")
        img.Graphic = graphic
        self._anchor(img, x, y, w, h)
        if mirrored:
            _set(img, VertMirrored=True)
        return img


# ==========================================================================
# format-neutral drivers
# ==========================================================================

def _place_text_box(backend, tb, page_no, x_offset=0, valign_override=None):
    """Place a text box plus any decorative border ornaments around it."""
    top_spec, bot_spec, pad_top, pad_bottom = border_pads(
        tb, backend.booksmart_dir)
    backend.text_box(tb, page_no, x_offset=x_offset,
                     valign_override=valign_override,
                     pad_top=pad_top, pad_bottom=pad_bottom)
    if not (top_spec or bot_spec) or not backend.booksmart_dir:
        return
    import bev
    for spec, is_top in ((top_spec, True), (bot_spec, False)):
        if not spec:
            continue
        stem, mirrored = spec
        path = _bev_path(stem, backend.booksmart_dir)
        if not os.path.exists(path):
            print('warning: border image %s not found, skipping' % path)
            continue
        svg = bev.decrypt_bev(path)
        w, h = _svg_dims(svg)
        graphic = backend.svg_graphic(svg)
        x = tb.x + tb.width / 2.0 - w / 2.0 + x_offset
        y = tb.y if is_top else tb.y + tb.height - h
        backend.ornament(graphic, w, h, x, y, mirrored)


def _build_cover(backend, bs, no_flaps):
    """Build the combined cover spread onto the next page (one page)."""
    parts, total_width, height = cover_spread(bs, no_flaps)
    backend.begin_page(total_width, height)
    x_off = 0
    for part in parts:
        # per-part background, then images, then text (cover text above photos)
        backend.bg_rect(x_off, 0, part.width, part.height, part.bgcolor)
        for ib in part.images:
            backend.image(ib, x_offset=x_off)
        for tb in part.text_boxes:
            valign_override = None
            if tb.rotation in (90, 270):
                tb.x = 0
                tb.width = part.width
                valign_override = VC
            _place_text_box(backend, tb, 0, x_offset=x_off,
                            valign_override=valign_override)
        x_off += part.width


def _build_body(backend, bs, page_limit=None):
    """Build the book body pages onto subsequent pages."""
    n = len(bs.pages) if page_limit is None else min(page_limit, len(bs.pages))
    for i in range(n):
        page_id = bs.pages[i]
        backend.begin_page(bs.width, bs.height, backend.page_bgcolor(page_id),
                           restart_number=(i == 0))
        # text boxes (with borders), then images on top.  page_no = book page
        # number (1-based), so $PageNumber ignores any preceding cover page.
        for tb in bs.text_boxes[page_id]:
            _place_text_box(backend, tb, i + 1)
        for ib in bs.images[page_id]:
            backend.image(ib)


def inject_book(backend, bs, no_flaps=True, page_limit=None):
    """Build the whole book: the combined cover spread as page 1 (jacket-wrap
    flaps omitted by default), then the body pages.

    The spread-sized cover and the body pages can only share one document on a
    backend with per-page sizing (Writer).  On a uniform-page-size backend
    (Draw) the cover is omitted here -- build it separately with inject_cover."""
    backend.init_styles(bs)
    backend._bs = bs
    backend.setup()
    if bs.cover and not backend.uniform_page_size:
        _build_cover(backend, bs, no_flaps)
    elif bs.cover:
        print("note: %s uses one page size for the whole document, so the cover "
              "spread can't share it with the body; building body only "
              "(use --cover-only for the cover spread)." % backend.default_ext)
    _build_body(backend, bs, page_limit)
    return backend.doc


def inject(backend, bs, page_limit=None):
    """Build only the book body (no cover)."""
    backend.init_styles(bs)
    backend._bs = bs
    backend.setup()
    _build_body(backend, bs, page_limit)
    return backend.doc


def inject_cover(backend, bs, no_flaps=True):
    """Build only the cover spread (one page)."""
    if not bs.cover:
        raise ValueError("This book has no cover to convert.")
    backend.init_styles(bs)
    backend._bs = bs
    backend.setup()
    _build_cover(backend, bs, no_flaps)
    return backend.doc


BACKENDS = {"odg": DrawBackend, "odt": WriterBackend}


# --------------------------------------------------------------------------
# standalone runner (headless soffice socket) -- not used by the filter
# --------------------------------------------------------------------------

def connect(port, timeout=30.0):
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
    ap = argparse.ArgumentParser(description="Inject a .book into a live "
                                 "LibreOffice document via UNO (standalone).")
    ap.add_argument("book_file")
    ap.add_argument("-f", "--format", choices=["odg", "odt"], default="odg",
                    help="target model: odg (Draw, default) or odt (Writer)")
    ap.add_argument("-p", "--port", type=int, default=2002)
    ap.add_argument("-o", "--output", help="output file (default: <book>.<ext>)")
    ap.add_argument("--pages", type=int,
                    help="only build the first N body pages")
    ap.add_argument("--flaps", action="store_true",
                    help="include the jacket-wrap flaps in the cover spread "
                         "(default: omit them)")
    ap.add_argument("--no-cover", action="store_true",
                    help="skip the cover spread (body pages only)")
    ap.add_argument("--cover-only", action="store_true",
                    help="build only the cover spread")
    ap.add_argument("-b", "--booksmart-dir",
                    help="BookSmart3 program dir, to render decorative "
                         "text-box borders (ornament .bev assets)")
    args = ap.parse_args()

    ctx, smgr, desktop = connect(args.port)

    # install the Pillow-free prober before parsing
    bookxml.probe_image = make_uno_prober(smgr, ctx)
    bs = bookxml.BookXML(args.book_file)
    print("Parsed: %s  (%dx%d pt, %d pages)" % (
        bs.info.get('booktitle', ''), bs.width, bs.height, len(bs.pages)))

    backend_cls = BACKENDS[args.format]
    doc = desktop.loadComponentFromURL(backend_cls.factory, "_blank", 0, ())
    backend = backend_cls(doc, smgr, ctx, booksmart_dir=args.booksmart_dir)
    if args.cover_only:
        inject_cover(backend, bs, no_flaps=not args.flaps)
    elif args.no_cover:
        inject(backend, bs, page_limit=args.pages)
    else:
        inject_book(backend, bs, no_flaps=not args.flaps, page_limit=args.pages)

    if args.output:
        out = args.output
    else:
        suffix = (" cover." if args.cover_only else ".") + backend_cls.default_ext
        out = os.path.splitext(args.book_file)[0] + suffix
    doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(out)),
                   (_pv("FilterName", backend_cls.filter_name),))
    doc.close(False)
    print("Wrote %s" % out)


if __name__ == "__main__":
    main()
