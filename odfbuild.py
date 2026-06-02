"""Shared builders for converting a BookXML book into OpenDocument content.

The page/body scaffolding differs between OpenDocument Text (.odt) and Drawing
(.odg) output, but the styles and the per-box frame content are identical.  Those
shared pieces live here so booksmart2odf.py (the ODT and ODG backends) can use
them.

A text/image box becomes a draw:frame positioned by svg:x/svg:y.  For ODT the
caller passes ``pageno`` so the frame gets page anchoring; for ODG ``pageno`` is
None (the frame is simply a child of its draw:page).
"""

import os
import math
from ezodf.const import ALL_NSMAP
from lxml.etree import Element

import bookxml
import odfcommon
import odfborder


def ns(combined_name):
    prefix, name = combined_name.split(':')
    return "{%s}%s" % (ALL_NSMAP[prefix], name)


# BookSmart text-box vertical alignment ('va') -> ODF draw:textarea-vertical-align.
# Absent/0 means top (the default), so it is left unmapped.
VALIGN_MAP = {3: 'middle', 4: 'bottom'}


def create_outer_frame(frameno, x, y, width, height, zindex,
                       transparent=False, pageno=None, layer=None,
                       style_name=None, x_offset=0, rotation=0):
    """Create the outer draw:frame for a text or image box.

    When ``pageno`` is given (ODT) the frame is page-anchored; when it is None
    (ODG) the frame carries no anchor and is positioned directly on its page.
    ``layer`` (e.g. "layout") is set for ODG, where every shape belongs to a
    drawing layer.  ``style_name`` overrides the default frame style.

    ``rotation`` (clockwise degrees, e.g. 90 for spine text) rotates the frame
    about the centre of its box via draw:transform.  For 90/270 the frame's
    layout width/height are swapped so the text flows along the long axis.
    """
    draw_frame = Element(ns('draw:frame'))
    draw_frame.attrib[ns('draw:name')] = 'Frame%d' % (frameno)
    if style_name is not None:
        draw_frame.attrib[ns('draw:style-name')] = style_name
    elif transparent:
        draw_frame.attrib[ns('draw:style-name')] = 'OuterFrameTextStyle'
    else:
        draw_frame.attrib[ns('draw:style-name')] = 'OuterFrameImageStyle'
    if layer is not None:
        draw_frame.attrib[ns('draw:layer')] = layer

    if rotation:
        bx = x + x_offset
        by = y
        cx = bx + width / 2.0   # box centre (rotation is about this point)
        cy = by + height / 2.0
        # swap layout dimensions for quarter turns so text runs the long way
        if rotation in (90, 270):
            sw, sh = float(height), float(width)
        else:
            sw, sh = float(width), float(height)
        beta = math.radians(rotation)  # BookSmart 'cr' rotates the opposite way
        a = math.cos(beta)
        b = math.sin(beta)
        c = -math.sin(beta)
        d = math.cos(beta)
        # map the frame's local origin so its box centre lands on (cx, cy)
        e = cx - a * (sw / 2.0) - c * (sh / 2.0)
        f = cy - b * (sw / 2.0) - d * (sh / 2.0)
        draw_frame.attrib[ns('svg:width')] = '%dpt' % sw
        draw_frame.attrib[ns('svg:height')] = '%dpt' % sh
        if layer is not None:
            # ODG / Draw honors the full matrix transform.
            draw_frame.attrib[ns('draw:transform')] = \
                'matrix(%g %g %g %g %gpt %gpt)' % (a, b, c, d, e, f)
        else:
            # ODT / Writer ignores a matrix transform but honors the equivalent
            # rotate()+translate() form.  Writer composes it as translate-after-
            # rotate (matrix = T(e,f)*R(theta)), so the translation is exactly
            # the matrix's own (e, f).
            theta = math.atan2(b, a)
            draw_frame.attrib[ns('draw:transform')] = \
                'rotate(%g) translate(%gpt %gpt)' % (-beta, bx + width, by)
    else:
        draw_frame.attrib[ns('svg:width')] = '%dpt' % width
        draw_frame.attrib[ns('svg:height')] = '%dpt' % height
        draw_frame.attrib[ns('svg:x')] = '%dpt' % (x + x_offset)
        draw_frame.attrib[ns('svg:y')] = '%dpt' % y

    if pageno is not None:
        draw_frame.attrib[ns('text:anchor-type')] = 'page'
        draw_frame.attrib[ns('text:anchor-page-number')] = '%d' % (pageno + 1)
    draw_frame.attrib[ns('draw:z-index')] = '%d' % (zindex + 1)

    return draw_frame


def build_bg_rect(bodf, x, y, width, height, color, name, zindex,
                  pageno=None, layer=None):
    """Build a solid-filled draw:rect, used for per-part cover backgrounds."""
    style_name = 'bgrect%s' % name
    ss = Element(ns('style:style'))
    ss.attrib[ns('style:family')] = 'graphic'
    ss.attrib[ns('style:name')] = style_name
    gp = Element(ns('style:graphic-properties'))
    gp.attrib[ns('draw:fill')] = 'solid'
    gp.attrib[ns('draw:fill-color')] = color
    gp.attrib[ns('draw:stroke')] = 'none'
    ss.append(gp)
    bodf.content.automatic_styles.xmlnode.append(ss)

    rect = Element(ns('draw:rect'))
    rect.attrib[ns('draw:name')] = 'BG%s' % name
    rect.attrib[ns('draw:style-name')] = style_name
    if layer is not None:
        rect.attrib[ns('draw:layer')] = layer
    rect.attrib[ns('svg:width')] = '%dpt' % width
    rect.attrib[ns('svg:height')] = '%dpt' % height
    rect.attrib[ns('svg:x')] = '%dpt' % x
    rect.attrib[ns('svg:y')] = '%dpt' % y
    if pageno is not None:
        rect.attrib[ns('text:anchor-type')] = 'page'
        rect.attrib[ns('text:anchor-page-number')] = '%d' % (pageno + 1)
    rect.attrib[ns('draw:z-index')] = '%d' % zindex
    return rect


def emit_metadata(bodf, bs):
    e = Element(ns('dc:creator'))
    if 'authorname' in bs.info:
        e.text = bs.info['authorname']
    else:
        e.text = ''
    bodf.meta.meta.append(e)
    e = Element(ns('dc:title'))
    e.text = bs.info['booktitle']
    bodf.meta.meta.append(e)


def emit_text_styles(bodf, bs):
    """Emit font-face, paragraph, and span automatic styles (content.xml)."""
    # Fonts entries
    for font in bs.fonts:
        ss_f = Element(ns('style:font-face'))
        ss_f.attrib[ns('style:name')] = font
        ss_f.attrib[ns('svg:font-family')] = font

        bodf.styles.fonts.xmlnode.append(ss_f)

        ss_f = Element(ns('style:font-face'))
        ss_f.attrib[ns('style:name')] = font
        ss_f.attrib[ns('svg:font-family')] = font
        bodf.content.fonts.xmlnode.append(ss_f)

    # Paragraph Styles
    print ("Writing paragraph styles.")

    for ps in bs.get_paragraph_styles():
        ss = Element(ns('style:style'))
        ss.attrib[ns('style:family')] = 'paragraph'
        ss.attrib[ns('style:name')] = ps['name']

        sp_p = Element(ns('style:paragraph-properties'))
        sp_p.attrib[ns('fo:text-align')] = bookxml.ParagraphStyle.ALIGN[ps['alignment']]
        sp_p.attrib[ns('style:justify-single-word')] = 'false'
        if ps['line_spacing']:
            # BookSmart specified extra line spacing; honor it
            sp_p.attrib[ns('fo:line-height')] = '%d%%' % ((ps['line_spacing'] + 1) * 100)
        else:
            # BookSmart didn't specify spacing; default frame text to 1.15
            sp_p.attrib[ns('fo:line-height')] = '115%'
        sp_p.attrib[ns('fo:margin-left')] = '%dpt' % ps['left_indent']

        ss.append(sp_p)

        st_p = Element(ns('style:text-properties'))
        if ps['bold']:
            st_p.attrib[ns('fo:font-weight')] = 'bold'
        if ps['italic']:
            st_p.attrib[ns('fo:font-style')] = 'italic'
        if ps['underline']:
            st_p.attrib[ns('style:text-underline-color')] = 'font-color'
            st_p.attrib[ns('style:text-underline-style')] = 'solid'
            st_p.attrib[ns('style:text-underline-width')] = 'auto'

        st_p.attrib[ns('fo:font-size')] = '%spt' % ps['size']
        st_p.attrib[ns('style:font-name')] = ps['font']
        if ps['color']:
            st_p.attrib[ns('fo:color')] = ps['color']

        ss.append(st_p)

        bodf.content.automatic_styles.xmlnode.append(ss)

    # Span Styles
    print ("Writing text styles.")
    for ts in bs.get_span_styles():
        ss = Element(ns('style:style'))
        ss.attrib[ns('style:family')] = 'text'
        ss.attrib[ns('style:name')] = ts['name']

        st_p = Element(ns('style:text-properties'))
        if ts['bold']:
            st_p.attrib[ns('fo:font-weight')] = 'bold'
        if ts['italic']:
            st_p.attrib[ns('fo:font-style')] = 'italic'
        if ts['underline']:
            st_p.attrib[ns('style:text-underline-color')] = 'font-color'
            st_p.attrib[ns('style:text-underline-style')] = 'solid'
            st_p.attrib[ns('style:text-underline-width')] = 'auto'

        if ts['size'] is not None:
            st_p.attrib[ns('fo:font-size')] = '%spt' % ts['size']
        if ts['font']:
            st_p.attrib[ns('style:font-name')] = ts['font']
        if ts['color']:
            st_p.attrib[ns('fo:color')] = ts['color']

        ss.append(st_p)

        bodf.content.automatic_styles.xmlnode.append(ss)


def emit_frame_styles(bodf, fill_none=False):
    """Emit the OuterFrameImageStyle / OuterFrameTextStyle graphic styles.

    ``fill_none`` is set for ODG: Draw shapes default to a solid fill, so the
    transparent text frames need explicit draw:fill="none"/draw:stroke="none"
    (the ODT-style draw:opacity="0%" is not honored the same way in Draw).
    """
    # LibreOffice Default Frame style
    style_style = Element(ns('style:style'))
    style_style.attrib[ns('style:family')] = 'graphic'
    style_style.attrib[ns('style:name')] = 'Frame'

    graphic_properties = Element(ns('style:graphic-properties'))
    graphic_properties.attrib[ns('fo:border')] = '0.06pt solid #000000'
    graphic_properties.attrib[ns('fo:margin-bottom')] = '0.0791in'
    graphic_properties.attrib[ns('fo:margin-top')] = '0.0791in'
    graphic_properties.attrib[ns('fo:margin-left')] = '0.0591in'
    graphic_properties.attrib[ns('fo:margin-right')] = '0.0591in'
    graphic_properties.attrib[ns('fo:padding')] = '0.0591n'
    graphic_properties.attrib[ns('style:horizontal-pos')] = 'center'
    graphic_properties.attrib[ns('style:vertical-pos')] = 'top'
    graphic_properties.attrib[ns('style:vertical-rel')] = 'paragraph-content'
    graphic_properties.attrib[ns('style:horizontal-rel')] = 'paragaph-content'
    graphic_properties.attrib[ns('style:wrap')] = 'parallel'


    # Outer frame style
    style_style = Element(ns('style:style'))
    style_style.attrib[ns('style:family')] = 'graphic'
    style_style.attrib[ns('style:name')] = 'OuterFrameImageStyle'
    if not fill_none:
        # 'Frame' is never actually emitted; Writer tolerates the dangling
        # parent, but Draw discards the style (and our fill) if it can't resolve
        # the parent, so omit it for ODG.
        style_style.attrib[ns('style:parent-style-name')] = 'Frame'

    graphic_properties = Element(ns('style:graphic-properties'))
    graphic_properties.attrib[ns('fo:border')] = 'none'
    graphic_properties.attrib[ns('fo:margin-bottom')] = '0in'
    graphic_properties.attrib[ns('fo:padding')] = '0in'
    graphic_properties.attrib[ns('style:horizontal-pos')] = 'from-left'
    graphic_properties.attrib[ns('style:vertical-pos')] = 'from-top'
    graphic_properties.attrib[ns('style:vertical-rel')] = 'page'
    graphic_properties.attrib[ns('style:horizontal-rel')] = 'page'
    graphic_properties.attrib[ns('style:wrap')] = 'run-through'
    if fill_none:
        graphic_properties.attrib[ns('draw:fill')] = 'none'
        graphic_properties.attrib[ns('draw:stroke')] = 'none'

    style_style.append(graphic_properties)
    bodf.content.automatic_styles.xmlnode.append(style_style)

    # Outer frame transparent style
    style_style = Element(ns('style:style'))
    style_style.attrib[ns('style:family')] = 'graphic'
    style_style.attrib[ns('style:name')] = 'OuterFrameTextStyle'
    if not fill_none:
        style_style.attrib[ns('style:parent-style-name')] = 'Frame'

    graphic_properties = Element(ns('style:graphic-properties'))
    graphic_properties.attrib[ns('fo:border')] = 'none'
    graphic_properties.attrib[ns('fo:margin-bottom')] = '0in'
    graphic_properties.attrib[ns('fo:padding')] = '0in'
    graphic_properties.attrib[ns('style:horizontal-pos')] = 'from-left'
    graphic_properties.attrib[ns('style:vertical-pos')] = 'from-top'
    graphic_properties.attrib[ns('style:vertical-rel')] = 'page'
    graphic_properties.attrib[ns('style:horizontal-rel')] = 'page'
    graphic_properties.attrib[ns('style:wrap')] = 'run-through'
    graphic_properties.attrib[ns('draw:opacity')] = '0%'
    if fill_none:
        graphic_properties.attrib[ns('draw:fill')] = 'none'
        graphic_properties.attrib[ns('draw:stroke')] = 'none'

    style_style.append(graphic_properties)
    bodf.content.automatic_styles.xmlnode.append(style_style)


def build_textbox(bodf, tb, frame_no, page_item_count, pageno=None,
                  booksmart_dir=None, tempdir=None, border_images=None,
                  layer=None, include_borders=True,
                  pad_top=0, pad_bottom=0, x_offset=0, valign=None):
    """Build the outer draw:frame for a text box (text-box + paragraphs + borders).

    Returns (outer_frame, frame_no) where frame_no has been advanced by any
    border ornament frames consumed.  The caller is responsible for attaching
    the frame and for the final page_item_count/frame_no increment of the box.

    ``include_borders`` is False for ODG, where in-flow ornament frames are not
    rendered by Draw; the caller places the border ornaments as separate page
    shapes instead.  ``pad_top``/``pad_bottom`` (points) inset the text inside
    the frame so it starts below a top ornament / stops above a bottom one.
    """
    # Apply the box's own vertical alignment (BookSmart 'va') unless the caller
    # forced one (e.g. the spine).
    if valign is None:
        valign = VALIGN_MAP.get(tb.valign)

    # A per-box style is needed when we inset the text (border ornament) or set
    # a vertical alignment.  Writer does not inherit frame positioning through an
    # automatic-style parent, so for ODT we mirror what LibreOffice itself emits:
    # parent the built-in "Frame" style and repeat the positioning inline.  For
    # ODG (layer set) the committed approach works (parent OuterFrameTextStyle,
    # explicit no-fill); Draw positions by svg:x/svg:y so positioning is moot.
    style_name = None
    if pad_top or pad_bottom or valign:
        style_name = 'textframe%d' % frame_no
        ts = Element(ns('style:style'))
        ts.attrib[ns('style:family')] = 'graphic'
        ts.attrib[ns('style:name')] = style_name
        tp = Element(ns('style:graphic-properties'))
        if layer is not None:
            # ODG / Draw: parent OuterFrameTextStyle, explicit no-fill.
            ts.attrib[ns('style:parent-style-name')] = 'OuterFrameTextStyle'
            tp.attrib[ns('draw:fill')] = 'none'
            tp.attrib[ns('draw:stroke')] = 'none'
        elif tb.rotation:
            # Rotated ODT box: this must be a drawing-shape "Text Box", not a
            # Writer "Frame", or Writer refuses to rotate it.  The distinction is
            # the style: a parentless graphic style with run-through="foreground"
            # (what LibreOffice emits for a text box) is a drawing shape;
            # parenting "Frame" would force a non-rotatable text frame.  The
            # rotate()+translate() transform on the frame does the placement.
            tp.attrib[ns('draw:stroke')] = 'none'
            tp.attrib[ns('draw:fill')] = 'none'
            tp.attrib[ns('style:run-through')] = 'foreground'
            tp.attrib[ns('style:wrap')] = 'run-through'
            tp.attrib[ns('style:vertical-pos')] = 'from-top'
            tp.attrib[ns('style:vertical-rel')] = 'page'
            tp.attrib[ns('style:horizontal-pos')] = 'from-left'
            tp.attrib[ns('style:horizontal-rel')] = 'page'
        else:
            # non-rotated ODT / Writer frame: Writer won't inherit the frame
            # positioning, so mirror what LibreOffice itself emits -- parent the
            # built-in "Frame" style and repeat the positioning inline.
            ts.attrib[ns('style:parent-style-name')] = 'Frame'
            tp.attrib[ns('fo:border')] = 'none'
            tp.attrib[ns('fo:padding')] = '0in'
            tp.attrib[ns('style:horizontal-pos')] = 'from-left'
            tp.attrib[ns('style:vertical-pos')] = 'from-top'
            tp.attrib[ns('style:vertical-rel')] = 'page'
            tp.attrib[ns('style:horizontal-rel')] = 'page'
            tp.attrib[ns('style:wrap')] = 'run-through'
            tp.attrib[ns('draw:opacity')] = '0%'
        if pad_top:
            tp.attrib[ns('fo:padding-top')] = '%gpt' % pad_top
        if pad_bottom:
            tp.attrib[ns('fo:padding-bottom')] = '%gpt' % pad_bottom
        if valign:
            tp.attrib[ns('draw:textarea-vertical-align')] = valign
        ts.append(tp)
        bodf.content.automatic_styles.xmlnode.append(ts)

    # create a transparent frame so text can live on top of images (if the z thing is right)
    outer_frame = create_outer_frame(frame_no, tb.x, tb.y,
                                     tb.width, tb.height, page_item_count,
                                     transparent=True, pageno=pageno, layer=layer,
                                     style_name=style_name, x_offset=x_offset,
                                     rotation=tb.rotation)

    # the text-box fills the frame; for quarter-turn rotation the frame's
    # layout height is the box's (unrotated) width
    local_height = tb.width if tb.rotation in (90, 270) else tb.height
    dtb = Element(ns('draw:text-box'))
    dtb.attrib[ns('fo:max-height')] = '%dpt' % local_height

    outer_frame.append(dtb)

    # decorative border ornaments flow with the text: top above, bottom
    # below (the bottom moves down as the text grows)
    top_spec = bot_spec = None
    if include_borders and tb.border and booksmart_dir:
        top_spec, bot_spec = odfborder.resolve_edges(tb.border)

    if top_spec:
        bp = odfborder.make_edge_paragraph(bodf, top_spec, tb.border,
                                           True, frame_no, booksmart_dir,
                                           tempdir, border_images)
        if bp is not None:
            dtb.append(bp)
            frame_no += 1

    for p in tb.paragraphs:
        paragraph = Element(ns('text:p'))
        if p.style:
            paragraph.attrib[ns('text:style-name')] = p.style

        for s in p.spans:
            if s.variable and not s.text.strip():
                continue

            span = Element(ns('text:span'))
            if s.style:
                span.attrib[ns('text:style-name')] = s.style

            if s.variable == '$PageNumber':
                span.append(Element(ns('text:page-number')))
            elif s.variable == '$BookTitle':
                span.append(Element(ns('text:title')))
            else:
                span.text = s.text
            paragraph.append(span)
        dtb.append(paragraph)

    if bot_spec:
        bp = odfborder.make_edge_paragraph(bodf, bot_spec, tb.border,
                                           False, frame_no, booksmart_dir,
                                           tempdir, border_images)
        if bp is not None:
            dtb.append(bp)
            frame_no += 1

    return outer_frame, frame_no


def prepare_images(images, **kwargs):
    """Fix DPI / cropping of a list of image boxes before they are emitted."""
    for ib in images:
        # if we're just linking the images, and if the image
        # requires its DPI adjusted, the default is to
        # make a copy in the same folder as it came from, fix
        # the DPI there, and then link to that. Optionally we
        # can fix the DPI in place, permanently modifying the
        # original image.
        if kwargs.get('link_images'):
            if kwargs.get('fix_in_place'):
                save_disk = bookxml.ImageBox.OVERWRITE
            else:
                save_disk = bookxml.ImageBox.SAVEASCOPY
            ib.fix_dpi(save_disk, **kwargs)
        elif kwargs.get('crop_images'):
            ib.crop_file()
        else:
            ib.fix_dpi(**kwargs)

        ib.calculate_crop()


def build_image(bodf, ib, frame_no, page_item_count, pageno=None,
                link_images=False, book_path=None, layer=None, flatten=False,
                x_offset=0):
    """Build the draw:frame for an image box (crop style + image).

    ODT (``flatten=False``) nests the image: an outer box frame containing a
    draw:text-box containing a frame-anchored subframe containing the image —
    this is how Writer emulates BookSmart's pan/zoom/crop.  Draw does not render
    that nesting, so ODG (``flatten=True``) instead places a single
    absolutely-positioned frame (carrying the same crop/mirror style) directly
    on the page at the displayed-image position.
    """
    # style to set up the image crop
    style_style = Element(ns('style:style'))
    style_style.attrib[ns('style:name')] = 'imageframe%d' % frame_no
    style_style.attrib[ns('style:family')] = 'graphic'
    style_style.attrib[ns('style:parent-style-name')] = 'Graphics'
    bodf.content.automatic_styles.xmlnode.append(style_style)

    style_graphic_properties = Element(ns('style:graphic-properties'))

    #TODO: if image itself is to be cropped in the zip bundle, set this rect to 0s
    style_graphic_properties.attrib[ns('fo:clip')] = \
             'rect(%fin, %fin, %fin, %fin)' % (ib.crop_top,
                                               ib.crop_right,
                                               ib.crop_bottom,
                                               ib.crop_left)
    style_graphic_properties.attrib[ns('draw:luminance')] = '0%'
    style_graphic_properties.attrib[ns('draw:contrast')] = '0%'
    style_graphic_properties.attrib[ns('draw:red')] = '0%'
    style_graphic_properties.attrib[ns('draw:green')] = '0%'
    style_graphic_properties.attrib[ns('draw:blue')] = '0%'
    style_graphic_properties.attrib[ns('draw:gamma')] = '100%'
    style_graphic_properties.attrib[ns('draw:color-inversion')] = 'false'
    style_graphic_properties.attrib[ns('draw:image-opacity')] = '100%'
    style_graphic_properties.attrib[ns('draw:color-mode')] = 'standard'
    if ib.vflip and ib.hflip:
        style_graphic_properties.attrib[ns('style:mirror')] = 'horizontal vertical'
    elif ib.vflip:
        style_graphic_properties.attrib[ns('style:mirror')] = 'vertical'
    elif ib.hflip:
        style_graphic_properties.attrib[ns('style:mirror')] = 'horizontal'
    style_style.append(style_graphic_properties)

    # the draw:image element (shared); embeds or links the picture
    draw_image = Element(ns('draw:image'))

    if link_images:
        # NOTE: Due to a long-standing bug in LibreOffice, DPI is not read from
        # linked image files, so using non-destructive cropping (preserving
        # original image files) does not work at all.  Effectively makes linking
        # images useless for our purposes.

        # embedding, calculate path
        image_path = '..' + ib.filename[len(book_path):]
    else:
        # create odf image to embed in zip file.  self registers
        # TODO if desired, crop image
        odf_image = odfcommon.ODFImageObject(bodf, ib.filename, ib.format)
        image_path = 'Pictures/' + os.path.basename(ib.filename)

    draw_image.attrib[ns('xlink:href')] = image_path
    draw_image.attrib[ns('xlink:type')] = 'simple'
    draw_image.attrib[ns('xlink:show')] = 'embed'
    draw_image.attrib[ns('xlink:actuate')] = 'onLoad'

    if flatten:
        # ODG: one absolutely-positioned frame carrying the crop/mirror style,
        # placed at the displayed-image position (box origin + pan offset).
        draw_frame = Element(ns('draw:frame'))
        draw_frame.attrib[ns('draw:name')] = 'ImageFrame%d' % (frame_no)
        draw_frame.attrib[ns('draw:style-name')] = 'imageframe%d' % frame_no
        if layer is not None:
            draw_frame.attrib[ns('draw:layer')] = layer
        draw_frame.attrib[ns('svg:width')] = '%dpt' % ib.width
        draw_frame.attrib[ns('svg:height')] = '%dpt' % ib.height
        draw_frame.attrib[ns('svg:x')] = '%dpt' % (ib.box_x + ib.x + x_offset)
        draw_frame.attrib[ns('svg:y')] = '%dpt' % (ib.box_y + ib.y)
        draw_frame.attrib[ns('draw:z-index')] = '%d' % (page_item_count + 1)
        draw_frame.append(draw_image)
        return draw_frame

    # ODT: outer box frame -> text-box -> subframe -> image.  The subframe
    # positions the image within the box to emulate BookSmart pan/zoom/crop.
    outer_frame = create_outer_frame(frame_no, ib.box_x, ib.box_y,
                                     ib.width, ib.height, page_item_count,
                                     transparent=False, pageno=pageno, layer=layer,
                                     x_offset=x_offset)

    draw_text_subbox = Element(ns('draw:text-box'))
    draw_text_subbox.attrib[ns('fo:max-height')] = '%dpt' % ib.height
    outer_frame.append(draw_text_subbox)

    draw_subframe = Element(ns('draw:frame'))
    draw_subframe.attrib[ns('draw:name')] = 'ImageFrame%d' % (frame_no)
    draw_subframe.attrib[ns('draw:style-name')] = 'imageframe%d' % frame_no
    draw_subframe.attrib[ns('svg:width')] = '%dpt' % ib.width
    draw_subframe.attrib[ns('svg:height')] = '%dpt' % ib.height
    draw_subframe.attrib[ns('svg:x')] = '%dpt' % ib.x
    draw_subframe.attrib[ns('svg:y')] = '%dpt' % ib.y
    draw_subframe.attrib[ns('text:anchor-type')] = 'frame'

    draw_text_subbox.append(draw_subframe)
    draw_subframe.append(draw_image)

    return outer_frame
