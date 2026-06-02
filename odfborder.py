"""Build ODF border-ornament paragraphs for a BookSmart text-box border.

Shared by book2odt.py and book2odg.py.  In BookSmart a text-box border places
an ornament image (a vector SVG from the theme library) within the text flow:
the top ornament sits above the text and the bottom ornament below it, so the
bottom ornament moves down as the text grows.  We reproduce that by inserting a
centered image paragraph at the start of the text box (top edge) and appending
one at the end (bottom edge), rather than absolutely positioning frames.

The ornament .bev files are DES-encrypted SVGs under
``<booksmart_dir>/resources/themes/library``; see bev.py and BEV_FORMAT.md.
"""

import os
from ezodf.const import ALL_NSMAP
from lxml.etree import Element

import bev
import bookxml
import odfcommon


def ns(combined_name):
    prefix, name = combined_name.split(':')
    return "{%s}%s" % (ALL_NSMAP[prefix], name)


def resolve_edges(border):
    """Return (top_spec, bot_spec) for a Border.

    Each spec is ``(image_stem, mirrored)`` or ``None``.  Declared top/bot edges
    are drawn as-is; when mirrorEdge == MIRROR_OPPOSITE a declared edge is also
    mirrored onto the (undeclared) opposite side.

    NOTE: We have only ever seen top/bot edges (with mirrorEdge 0 or 2) in real
    BookSmart books.  left/right edges and MIRROR_ALL have never turned up, so
    they are unsupported and ignored with a warning.  If a book ever uses them,
    we'll figure out how to draw them then.
    """
    top = ('top' in border.edges) and (border.edges['top'], False) or None
    bot = ('bot' in border.edges) and (border.edges['bot'], False) or None

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


def make_edge_paragraph(bodf, spec, border, is_top, frame_no,
                        booksmart_dir, tempdir, registered):
    """Build a centered text:p containing the border ornament image.

    Returns the ``text:p`` Element, or None if the ornament could not be
    resolved (missing .bev).  Embeds the decrypted SVG once per stem.
    """
    stem, mirrored = spec
    library = os.path.join(booksmart_dir, 'resources', 'themes', 'library')

    bev_path = os.path.join(library, stem + '.bev')
    if not os.path.exists(bev_path):
        print('warning: border image %s not found, skipping' % bev_path)
        return None

    svg_bytes = bev.decrypt_bev(bev_path)
    width, height = bev.svg_dimensions(svg_bytes)

    # When reflecting an ornament onto the opposite edge it must be flipped
    # across the horizontal axis (top<->bottom).  LibreOffice doesn't reliably
    # honor style:mirror on embedded SVG, so bake the flip into a separate SVG
    # and embed that instead.
    if mirrored:
        image_name = stem + '_flipv'
        image_bytes = bev.flip_svg_vertical(svg_bytes)
    else:
        image_name = stem
        image_bytes = svg_bytes

    if image_name not in registered:
        svg_path = os.path.join(tempdir, image_name + '.svg')
        with open(svg_path, 'wb') as f:
            f.write(image_bytes)
        odfcommon.ODFImageObject(bodf, svg_path, 'svg+xml')
        registered.add(image_name)

    # paragraph style: center the ornament; no extra spacing so the text
    # immediately follows the top ornament and the bottom ornament immediately
    # follows the last line of text
    para_style = 'borderpara%d' % frame_no
    ps = Element(ns('style:style'))
    ps.attrib[ns('style:family')] = 'paragraph'
    ps.attrib[ns('style:name')] = para_style
    pp = Element(ns('style:paragraph-properties'))
    pp.attrib[ns('fo:text-align')] = 'center'
    pp.attrib[ns('fo:margin-top')] = '0pt'
    pp.attrib[ns('fo:margin-bottom')] = '0pt'
    ps.append(pp)
    bodf.content.automatic_styles.xmlnode.append(ps)

    # graphic style: ornament anchored to the paragraph, centered, no border
    img_style = 'borderimg%d' % frame_no
    gs = Element(ns('style:style'))
    gs.attrib[ns('style:family')] = 'graphic'
    gs.attrib[ns('style:name')] = img_style
    gp = Element(ns('style:graphic-properties'))
    gp.attrib[ns('fo:border')] = 'none'
    gp.attrib[ns('fo:padding')] = '0in'
    gp.attrib[ns('style:wrap')] = 'none'
    gp.attrib[ns('style:horizontal-pos')] = 'center'
    gp.attrib[ns('style:horizontal-rel')] = 'paragraph'
    gp.attrib[ns('style:vertical-pos')] = 'top'
    gp.attrib[ns('style:vertical-rel')] = 'paragraph'
    gs.append(gp)
    bodf.content.automatic_styles.xmlnode.append(gs)

    paragraph = Element(ns('text:p'))
    paragraph.attrib[ns('text:style-name')] = para_style

    frame = Element(ns('draw:frame'))
    frame.attrib[ns('draw:name')] = 'Border%d' % frame_no
    frame.attrib[ns('draw:style-name')] = img_style
    frame.attrib[ns('svg:width')] = '%gpt' % width
    frame.attrib[ns('svg:height')] = '%gpt' % height
    frame.attrib[ns('text:anchor-type')] = 'paragraph'

    image = Element(ns('draw:image'))
    image.attrib[ns('xlink:href')] = 'Pictures/%s.svg' % image_name
    image.attrib[ns('xlink:type')] = 'simple'
    image.attrib[ns('xlink:show')] = 'embed'
    image.attrib[ns('xlink:actuate')] = 'onLoad'
    frame.append(image)
    paragraph.append(frame)

    return paragraph
