#!/usr/bin/python3
import os
import sys
import ezodf
from ezodf.const import ALL_NSMAP
from lxml.etree import QName, Element
import bookxml
import odfcommon
import odfborder
import odfbuild
from odfbuild import ns


def setup_odt(bodf, bs):
    # set up metadata
    odfbuild.emit_metadata(bodf, bs)

    # set up page styles
    print ("Writing page styles.")
    for page_style in bs.get_page_styles():
        sp_l = Element(ns('style:page-layout'))
        sp_l.attrib[ns('style:name')] = 'M%s' % page_style['name']

        sp_l_p = Element(ns('style:page-layout-properties'))
        sp_l_p.attrib[ns('fo:margin-bottom')] = '54pt'
        sp_l_p.attrib[ns('fo:margin-top')] = '54pt'
        sp_l_p.attrib[ns('fo:margin-left')] = '54pt'
        sp_l_p.attrib[ns('fo:margin-right')] = '54pt'
        sp_l_p.attrib[ns('fo:page-height')] = '%fpt' % bs.height
        sp_l_p.attrib[ns('fo:page-width')] = '%fpt' % bs.width
        #sp_l_p.attrib[ns('style:footnote-max-height')] = '0pt'
        sp_l_p.attrib[ns('style:num-format')] = '1' # locale specific?

        if (bs.width > bs.height):
            sp_l_p.attrib[ns('style:print-orientation')] = 'landscape'
        else:
            sp_l_p.attrib[ns('style:print-orientation')] = 'portrait'
        sp_l_p.attrib[ns('style:writing-mode')] = 'lr-tb' # probably all Booksmart supported

        if page_style['bgcolor'] != '#ffffff':
            sp_l_p.attrib[ns('draw:fill')] = 'solid'
            sp_l_p.attrib[ns('draw:fill-color')] = page_style['bgcolor']
            sp_l_p.attrib[ns('fo:background-color')] = page_style['bgcolor']

        sp_l.append(sp_l_p)
        bodf.styles.automatic_styles.xmlnode.append(sp_l)

        sm_p = Element(ns('style:master-page'))

        sm_p.attrib[ns('style:name')] = page_style['name']
        sm_p.attrib[ns('style:page-layout-name')] = 'M%s' % page_style['name']

        bodf.styles.master_styles.xmlnode.append(sm_p)

        # create a page break paragraph style that switches to this page style
        ss = Element(ns('style:style'))
        ss.attrib[ns('style:family')] = 'paragraph'
        ss.attrib[ns('style:name')] = '%sbreak' % page_style['name']
        ss.attrib[ns('style:master-page-name')] = '%s' % page_style['name']

        sp_p = Element(ns('style:paragraph-properties'))
        sp_p.attrib[ns('fo:break-before')] = 'page'

        ss.append(sp_p)

        bodf.content.automatic_styles.xmlnode.append(ss)

    # set up default page style standard
    sm_p = Element(ns('style:master-page'))
    sm_p.attrib[ns('style:name')] = 'Standard'
    sm_p.attrib[ns('style:page-layout-name')] = "M%s" % bs.page_info[bs.pages[0]]['page_style']
    bodf.styles.master_styles.xmlnode.append(sm_p)

    odfbuild.emit_text_styles(bodf, bs)
    odfbuild.emit_frame_styles(bodf)


def process_odt_pages(bodf, bs, **kwargs):
    #process pages
    print ("Converting pages...")

    frame_no = 0
    booksmart_dir = kwargs.get('booksmart_dir')
    border_images = set() # image stems already embedded for borders

    for page_no, page in enumerate(bs.pages):
        page_item_count = 0

        print ("Page %d... " % (page_no+1),end='')

        if page_no > 0:
            # emit a page break
            p = Element(ns('text:p'))

            if 'pagination' in bs.page_info[page] and \
               bs.page_info[page]['pagination'] == 'START_PAGE_NUMBERS':
                # create a paragraph style that breaks the page and sets the page number to 1
                ss = Element(ns('style:style'))
                ss.attrib[ns('style:family')] = 'paragraph'
                ss.attrib[ns('style:name')] = '%sbreakresetpageno' % bs.page_info[page]['page_style']
                ss.attrib[ns('style:master-page-name')] = '%s' % bs.page_info[page]['page_style']

                sp_p = Element(ns('style:paragraph-properties'))
                sp_p.attrib[ns('fo:break-before')] = 'page'
                sp_p.attrib[ns('style:page-number')] = '1'

                ss.append(sp_p)

                bodf.content.automatic_styles.xmlnode.append(ss)

                p.attrib[ns('text:style-name')] = '%sbreakresetpageno' % bs.page_info[page]['page_style']
            else:
                p.attrib[ns('text:style-name')] = '%sbreak' % bs.page_info[page]['page_style']
        else:
            p = Element(ns('text:p'))
        bodf.body.xmlnode.append(p)

        # Text boxes
        print ('text boxes...', end='')
        for tb in bs.text_boxes[page]:
            outer_frame, frame_no = odfbuild.build_textbox(
                bodf, tb, frame_no, page_item_count, pageno=page_no,
                booksmart_dir=booksmart_dir, tempdir=kwargs['tempdir'],
                border_images=border_images)
            bodf.body.xmlnode.insert(2, outer_frame)

            page_item_count +=1
            frame_no +=1

        print ('fixing dpi and cropping...', end='')
        odfbuild.prepare_images(bs.images[page], **kwargs)

        print ('image boxes...', end='')
        for ib in bs.images[page]:
            outer_frame = odfbuild.build_image(
                bodf, ib, frame_no, page_item_count, pageno=page_no,
                link_images=kwargs.get('link_images', False),
                book_path=bs.book_path)
            bodf.body.xmlnode.insert(2, outer_frame)

            page_item_count +=1
            frame_no +=1
        print ('done.')


def setup_odg(bodf, bs):
    # set up metadata
    odfbuild.emit_metadata(bodf, bs)

    # set up page styles: each distinct BookSmart page style becomes a master
    # page (sized page-layout + a drawing-page style carrying the background)
    print ("Writing page styles.")
    for page_style in bs.get_page_styles():
        sp_l = Element(ns('style:page-layout'))
        sp_l.attrib[ns('style:name')] = 'M%s' % page_style['name']

        sp_l_p = Element(ns('style:page-layout-properties'))
        sp_l_p.attrib[ns('fo:margin')] = '0pt'
        sp_l_p.attrib[ns('fo:page-height')] = '%fpt' % bs.height
        sp_l_p.attrib[ns('fo:page-width')] = '%fpt' % bs.width

        if (bs.width > bs.height):
            sp_l_p.attrib[ns('style:print-orientation')] = 'landscape'
        else:
            sp_l_p.attrib[ns('style:print-orientation')] = 'portrait'

        sp_l.append(sp_l_p)
        bodf.styles.automatic_styles.xmlnode.append(sp_l)

        # drawing-page style holds the page background colour
        dp = Element(ns('style:style'))
        dp.attrib[ns('style:family')] = 'drawing-page'
        dp.attrib[ns('style:name')] = 'dp%s' % page_style['name']
        dp_p = Element(ns('style:drawing-page-properties'))
        if page_style['bgcolor'] != '#ffffff':
            dp_p.attrib[ns('draw:fill')] = 'solid'
            dp_p.attrib[ns('draw:fill-color')] = page_style['bgcolor']
        else:
            dp_p.attrib[ns('draw:fill')] = 'none'
        dp.append(dp_p)
        bodf.styles.automatic_styles.xmlnode.append(dp)

        sm_p = Element(ns('style:master-page'))
        sm_p.attrib[ns('style:name')] = page_style['name']
        sm_p.attrib[ns('style:page-layout-name')] = 'M%s' % page_style['name']
        sm_p.attrib[ns('draw:style-name')] = 'dp%s' % page_style['name']
        bodf.styles.master_styles.xmlnode.append(sm_p)

    # default master page
    sm_p = Element(ns('style:master-page'))
    sm_p.attrib[ns('style:name')] = 'Standard'
    sm_p.attrib[ns('style:page-layout-name')] = "M%s" % bs.page_info[bs.pages[0]]['page_style']
    bodf.styles.master_styles.xmlnode.append(sm_p)

    odfbuild.emit_text_styles(bodf, bs)
    odfbuild.emit_frame_styles(bodf, fill_none=True)


def process_odg_pages(bodf, bs, **kwargs):
    #process pages
    print ("Converting pages...")

    frame_no = 0
    booksmart_dir = kwargs.get('booksmart_dir')
    border_images = set() # image stems already embedded for borders

    # office:drawing must contain only draw:page children; drop the
    # text:variable-decls / text:user-field-decls ezodf adds for every body,
    # and any default page it created.
    for child in list(bodf.body.xmlnode):
        bodf.body.xmlnode.remove(child)

    for page_no, page in enumerate(bs.pages):
        page_item_count = 0

        print ("Page %d... " % (page_no+1),end='')

        draw_page = Element(ns('draw:page'))
        draw_page.attrib[ns('draw:name')] = 'page%d' % (page_no + 1)
        draw_page.attrib[ns('draw:master-page-name')] = bs.page_info[page]['page_style']
        draw_page.attrib[ns('draw:style-name')] = 'dp%s' % bs.page_info[page]['page_style']

        # Text boxes
        print ('text boxes...', end='')
        for tb in bs.text_boxes[page]:
            # Draw can't reserve space for the absolute ornament shapes, so
            # measure them up front and inset the text box's top/bottom padding
            # so the text starts below a top ornament / stops above a bottom one.
            top_spec = bot_spec = None
            pad_top = pad_bottom = 0
            if tb.border and booksmart_dir:
                top_spec, bot_spec = odfborder.resolve_edges(tb.border)
                if top_spec:
                    size = odfborder.edge_image_size(top_spec, booksmart_dir)
                    if size:
                        pad_top = size[1]
                if bot_spec:
                    size = odfborder.edge_image_size(bot_spec, booksmart_dir)
                    if size:
                        pad_bottom = size[1]

            outer_frame, frame_no = odfbuild.build_textbox(
                bodf, tb, frame_no, page_item_count, pageno=None,
                booksmart_dir=booksmart_dir, tempdir=kwargs['tempdir'],
                border_images=border_images, layer='layout',
                include_borders=False, pad_top=pad_top, pad_bottom=pad_bottom)
            draw_page.append(outer_frame)

            page_item_count +=1
            frame_no +=1

            # place the border ornaments as separate absolutely-positioned page
            # shapes (centered on the text box, at the top/bottom edge).
            for spec, is_top in ((top_spec, True), (bot_spec, False)):
                if not spec:
                    continue
                bf = odfborder.make_edge_frame(
                    bodf, spec, tb, is_top, frame_no, booksmart_dir,
                    kwargs['tempdir'], border_images, page_item_count,
                    layer='layout')
                if bf is not None:
                    draw_page.append(bf)
                    page_item_count += 1
                    frame_no += 1

        print ('fixing dpi and cropping...', end='')
        odfbuild.prepare_images(bs.images[page], **kwargs)

        print ('image boxes...', end='')
        for ib in bs.images[page]:
            outer_frame = odfbuild.build_image(
                bodf, ib, frame_no, page_item_count, pageno=None,
                link_images=kwargs.get('link_images', False),
                book_path=bs.book_path, layer='layout', flatten=True)
            draw_page.append(outer_frame)

            page_item_count +=1
            frame_no +=1

        bodf.body.xmlnode.append(draw_page)
        print ('done.')


# print-wrap order of cover parts, left to right (outside facing up)
COVER_PRINT_ORDER = ['Back Flap', 'Back Cover', 'Spine', 'Front Cover', 'Front Flap']


def cover_spread(bs):
    """Return (ordered_parts, total_width, height) for the print-wrap spread."""
    def order(part):
        try:
            return COVER_PRINT_ORDER.index(part.title)
        except ValueError:
            return len(COVER_PRINT_ORDER) # unrecognized parts go last, in list order
    parts = sorted(bs.cover, key=order)
    total_width = sum(p.width for p in parts)
    height = max(p.height for p in parts)
    return parts, total_width, height


def setup_cover(bodf, bs, fmt):
    odfbuild.emit_metadata(bodf, bs)

    parts, total_width, height = cover_spread(bs)

    # one page layout sized to the whole spread
    sp_l = Element(ns('style:page-layout'))
    sp_l.attrib[ns('style:name')] = 'Mcover'
    sp_l_p = Element(ns('style:page-layout-properties'))
    sp_l_p.attrib[ns('fo:margin')] = '0pt'
    sp_l_p.attrib[ns('fo:page-width')] = '%fpt' % total_width
    sp_l_p.attrib[ns('fo:page-height')] = '%fpt' % height
    if total_width > height:
        sp_l_p.attrib[ns('style:print-orientation')] = 'landscape'
    else:
        sp_l_p.attrib[ns('style:print-orientation')] = 'portrait'
    sp_l.append(sp_l_p)
    bodf.styles.automatic_styles.xmlnode.append(sp_l)

    if fmt == 'odg':
        dp = Element(ns('style:style'))
        dp.attrib[ns('style:family')] = 'drawing-page'
        dp.attrib[ns('style:name')] = 'dpcover'
        dp_p = Element(ns('style:drawing-page-properties'))
        dp_p.attrib[ns('draw:fill')] = 'none'
        dp.append(dp_p)
        bodf.styles.automatic_styles.xmlnode.append(dp)

    sm_p = Element(ns('style:master-page'))
    sm_p.attrib[ns('style:name')] = 'cover'
    sm_p.attrib[ns('style:page-layout-name')] = 'Mcover'
    if fmt == 'odg':
        sm_p.attrib[ns('draw:style-name')] = 'dpcover'
    bodf.styles.master_styles.xmlnode.append(sm_p)

    # default master page (used by the single ODT page)
    sm_p = Element(ns('style:master-page'))
    sm_p.attrib[ns('style:name')] = 'Standard'
    sm_p.attrib[ns('style:page-layout-name')] = 'Mcover'
    bodf.styles.master_styles.xmlnode.append(sm_p)

    odfbuild.emit_text_styles(bodf, bs)
    odfbuild.emit_frame_styles(bodf, fill_none=(fmt == 'odg'))


def process_cover(bodf, bs, fmt, **kwargs):
    print ("Converting cover...")

    parts, total_width, height = cover_spread(bs)
    booksmart_dir = kwargs.get('booksmart_dir')
    border_images = set()
    frame_no = 0
    page_item_count = 0

    if fmt == 'odg':
        # office:drawing must contain only draw:page children
        for child in list(bodf.body.xmlnode):
            bodf.body.xmlnode.remove(child)
        draw_page = Element(ns('draw:page'))
        draw_page.attrib[ns('draw:name')] = 'cover'
        draw_page.attrib[ns('draw:master-page-name')] = 'cover'
        draw_page.attrib[ns('draw:style-name')] = 'dpcover'
        attach = draw_page.append
        pageno, layer, flatten = None, 'layout', True
    else:
        bodf.body.xmlnode.append(Element(ns('text:p')))
        attach = lambda el: bodf.body.xmlnode.insert(2, el)
        pageno, layer, flatten = 0, None, False

    x_off = 0
    for part in parts:
        print ('%s...' % part.title, end='')

        # per-part background rectangle (lowest in the part's stack)
        attach(odfbuild.build_bg_rect(bodf, x_off, 0, part.width, part.height,
                                      part.bgcolor, frame_no, page_item_count,
                                      pageno=pageno, layer=layer))
        page_item_count += 1
        frame_no += 1

        odfbuild.prepare_images(part.images, **kwargs)
        for ib in part.images:
            attach(odfbuild.build_image(
                bodf, ib, frame_no, page_item_count, pageno=pageno,
                link_images=kwargs.get('link_images', False),
                book_path=bs.book_path, layer=layer, flatten=flatten,
                x_offset=x_off))
            page_item_count += 1
            frame_no += 1

        for tb in part.text_boxes:
            top_spec = bot_spec = None
            pad_top = pad_bottom = 0
            if fmt == 'odg' and tb.border and booksmart_dir:
                top_spec, bot_spec = odfborder.resolve_edges(tb.border)
                if top_spec:
                    size = odfborder.edge_image_size(top_spec, booksmart_dir)
                    if size:
                        pad_top = size[1]
                if bot_spec:
                    size = odfborder.edge_image_size(bot_spec, booksmart_dir)
                    if size:
                        pad_bottom = size[1]

            outer_frame, frame_no = odfbuild.build_textbox(
                bodf, tb, frame_no, page_item_count, pageno=pageno,
                booksmart_dir=booksmart_dir, tempdir=kwargs['tempdir'],
                border_images=border_images, layer=layer,
                include_borders=(fmt != 'odg'), pad_top=pad_top,
                pad_bottom=pad_bottom, x_offset=x_off)
            attach(outer_frame)
            page_item_count += 1
            frame_no += 1

            if fmt == 'odg':
                for spec, is_top in ((top_spec, True), (bot_spec, False)):
                    if not spec:
                        continue
                    bf = odfborder.make_edge_frame(
                        bodf, spec, tb, is_top, frame_no, booksmart_dir,
                        kwargs['tempdir'], border_images, page_item_count,
                        layer=layer, x_offset=x_off)
                    if bf is not None:
                        attach(bf)
                        page_item_count += 1
                        frame_no += 1

        x_off += part.width

    if fmt == 'odg':
        bodf.body.xmlnode.append(draw_page)
    print ('done.')


if __name__ == "__main__":
    import tempfile
    import argparse


    argparser = argparse.ArgumentParser()
    argparser.add_argument('-o','--output', type=str, help='write the converted document to OUTPUT. Default is to write it with the same name as the book, with the format extension, in the same folder as the book file.')
    argparser.add_argument('-f','--format', choices=['odt','odg'], default='odt',
                           help='output format: odt (OpenDocument Text, the default) or odg (OpenDocument Drawing).')
    argparser.add_argument('-c','--crop', action='store_const', const=True,
                           help='Crop the images stored in the zip file, rather than just zoom and soft crop them. Leaves original image files alone.')
    argparser.add_argument('-b','--booksmart-dir', type=str,
                           help='Path to the BookSmart3 program directory. Required to render decorative text-box borders (the ornament images live encrypted under resources/themes/library). If omitted, borders are skipped.')
    argparser.add_argument('--cover', action='store_true',
                           help='Convert the book cover (as its own combined-spread file) instead of the book body. The cover is normally submitted to the publisher as a separate PDF.')

    argparser.add_argument('book_file', type=str, help='book file to convert.')

    args = argparser.parse_args()

    bookfile = args.book_file
    bookpath = os.path.dirname(os.path.abspath(bookfile))

    if args.output:
        odffile = args.output
    elif args.cover:
        odffile = os.path.splitext(bookfile)[0] + ' cover.' + args.format
    else:
        odffile = os.path.splitext(bookfile)[0] + '.' + args.format

    print (odffile)

    bs = bookxml.BookXML(bookfile)
    bodf = ezodf.newdoc(args.format, odffile)

    print ("Converting %s\n        to %s." % (bookfile, odffile))

    print (bs.info['booktitle'])
    print (bs.info['subtitle'])
    if 'authorname' in bs.info:
        print (bs.info['authorname'])
    print ('Width: %d, Height: %d' % (bs.width, bs.height))
    print ('Pages: %d' % len(bs.pages))

    with  tempfile.TemporaryDirectory(prefix='bookxml') as tempdir:
        if args.cover:
            if not bs.cover:
                sys.exit('This book has no cover to convert.')
            setup_cover(bodf, bs, args.format)
            process_cover(bodf, bs, args.format, tempdir=tempdir,
                          crop_images=args.crop, booksmart_dir=args.booksmart_dir)
        elif args.format == 'odg':
            setup_odg(bodf, bs)
            process_odg_pages(bodf, bs, tempdir=tempdir, crop_images=args.crop,
                              booksmart_dir=args.booksmart_dir)
        else:
            setup_odt(bodf, bs)
            process_odt_pages(bodf, bs, tempdir=tempdir, crop_images=args.crop,
                              booksmart_dir=args.booksmart_dir)
        print ('Saving %s file. May take a few minutes to store all the images.' % args.format)
        bodf.save()
