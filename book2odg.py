#!/usr/bin/python3
import os
import sys
import ezodf
from ezodf.const import ALL_NSMAP
from lxml.etree import QName, Element
import bookxml
import odfcommon
import odfborder

def ns(combined_name):
    prefix,name = combined_name.split(':')
    return "{%s}%s" % (ALL_NSMAP[prefix], name)

def create_frame(pageno, frameno, x,y, width, height, zindex, transparent=False):
    draw_frame = Element(ns('draw:frame'))
    draw_frame.attrib[ns('draw:name')] = 'Frame%d' % (frameno)
    if transparent:
        draw_frame.attrib[ns('draw:style-name')] = 'OuterFrameTextStyle'
    else:
        draw_frame.attrib[ns('draw:style-name')] = 'OuterFrameImageStyle'
    draw_frame.attrib[ns('svg:width')] = '%dpt' % width
    draw_frame.attrib[ns('svg:height')] = '%dpt' % height
    draw_frame.attrib[ns('svg:x')] = '%dpt' % x
    draw_frame.attrib[ns('svg:y')] = '%dpt' % y
    draw_frame.attrib[ns('text:anchor-type')] = 'page'
    draw_frame.attrib[ns('text:anchor-page-number')] = '%d' % (pageno + 1)
    draw_frame.attrib[ns('draw:z-index')] = '%d' % (zindex + 1)

    return draw_frame

def setup_odt(bodf, bs):
    # set up metadata
    e = Element(ns('dc:creator'))
    e.text = bs.info['authorname']
    bodf.meta.meta.append(e)
    e = Element(ns('dc:title'))
    e.text = bs.info['booktitle']
    bodf.meta.meta.append(e)

    # set up page styles
    #print (bs.width, bs.height)
    print ("Writing page styles.")
    for page_style in bs.get_page_styles():
        #print (page_style)
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
        #print ((ps['line_spacing']+1) * 100 )
        sp_p.attrib[ns('fo:line-height')] = '%d%%' % ((ps['line_spacing'] + 1) * 100)
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
        #print (ts)
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
    style_style.attrib[ns('style:parent-style-name')] = 'Frame'

    graphic_properties = Element(ns('style:graphic-properties'))
    graphic_properties.attrib[ns('fo:border')] = 'none'
    graphic_properties.attrib[ns('fo:margin-bottom')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-top')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-left')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-right')] = '0in'
    graphic_properties.attrib[ns('fo:padding')] = '0in'
    graphic_properties.attrib[ns('style:horizontal-pos')] = 'from-left'
    graphic_properties.attrib[ns('style:vertical-pos')] = 'from-top'
    graphic_properties.attrib[ns('style:vertical-rel')] = 'page'
    graphic_properties.attrib[ns('style:horizontal-rel')] = 'page'
    graphic_properties.attrib[ns('style:wrap')] = 'run-through'

    style_style.append(graphic_properties)
    bodf.content.automatic_styles.xmlnode.append(style_style)

    # Outer frame transparent style
    style_style = Element(ns('style:style'))
    style_style.attrib[ns('style:family')] = 'graphic'
    style_style.attrib[ns('style:name')] = 'OuterFrameTextStyle'
    style_style.attrib[ns('style:parent-style-name')] = 'Frame'

    graphic_properties = Element(ns('style:graphic-properties'))
    graphic_properties.attrib[ns('fo:border')] = 'none'
    graphic_properties.attrib[ns('fo:margin-bottom')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-top')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-left')] = '0in'
    #graphic_properties.attrib[ns('fo:margin-right')] = '0in'
    graphic_properties.attrib[ns('fo:padding')] = '0in'
    graphic_properties.attrib[ns('style:horizontal-pos')] = 'from-left'
    graphic_properties.attrib[ns('style:vertical-pos')] = 'from-top'
    graphic_properties.attrib[ns('style:vertical-rel')] = 'page'
    graphic_properties.attrib[ns('style:horizontal-rel')] = 'page'
    graphic_properties.attrib[ns('style:wrap')] = 'run-through'
    graphic_properties.attrib[ns('draw:opacity')] = '0%'


    style_style.append(graphic_properties)
    bodf.content.automatic_styles.xmlnode.append(style_style)
def process_odt_pages(bodt, bs, **kwargs):
    #process pages
    print ("Converting pages...")

    frame_no = 0
    booksmart_dir = kwargs.get('booksmart_dir')
    border_images = set() # image stems already embedded for borders

    #for page_no, page in enumerate([ bs.pages[1] ]):
    for page_no, page in enumerate(bs.pages):
        page_item_count = 0

        print ("Page %d... " % (page_no+1),end='')
        #print (bs.page_info[page])

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
            #create a transparent frame so text can live on top of images (if the z thing is right)
            outer_frame = create_frame(page_no, frame_no, tb.x, tb.y, 
                                       tb.width, tb.height, page_item_count,
                                       transparent = True)

            bodf.body.xmlnode.insert(2,outer_frame)

            dtb = Element(ns('draw:text-box'))
            dtb.attrib[ns('fo:max-height')] = '%dpt' % tb.height

            outer_frame.append(dtb)

            # decorative border ornaments flow with the text: top above, bottom
            # below (the bottom moves down as the text grows)
            top_spec = bot_spec = None
            if tb.border and booksmart_dir:
                top_spec, bot_spec = odfborder.resolve_edges(tb.border)

            if top_spec:
                bp = odfborder.make_edge_paragraph(bodf, top_spec, tb.border,
                                                   True, frame_no, booksmart_dir,
                                                   kwargs['tempdir'], border_images)
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
                                                   kwargs['tempdir'], border_images)
                if bp is not None:
                    dtb.append(bp)
                    frame_no += 1

            page_item_count +=1
            frame_no +=1

        print ('fixing dpi and cropping...', end='')
        for ib in bs.images[page]:
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

        print ('image boxes...', end='')
        
        for ib in bs.images[page]:
            outer_frame = create_frame(page_no, frame_no, ib.box_x, ib.box_y, ib.width, ib.height, page_item_count)
            bodf.body.xmlnode.insert(2,outer_frame)

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

            # place image at x,y, set display width and height
            # sub-frame around graphic so we can position image properly within the
            # frame. This is to emulate the way Booksmart allows placing of images,
            # zooming, panning, etc
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

            draw_image = Element(ns('draw:image'))

            if kwargs.get('link_images', False):
                # NOTE: Due to a long-standing bug in LibreOffice, DPI is not read from
                # linked image files, so using non-destructive cropping (preserving
                # original image files) does not work at all.  Effectively makes linking
                # images useless for our purposes.

                # embedding, calculate path
                image_path = '..' + ib.filename[len(bs.book_path):]
            else:
                # create odf image to embed in zip file.  self registers
                # TODO if desired, crop image
                odf_image = odfcommon.ODFImageObject(bodf, ib.filename, ib.format)
                image_path = 'Pictures/' + os.path.basename(ib.filename)

            draw_image.attrib[ns('xlink:href')] = image_path
            draw_image.attrib[ns('xlink:type')] = 'simple'
            draw_image.attrib[ns('xlink:show')] = 'embed'
            draw_image.attrib[ns('xlink:actuate')] = 'onLoad'
            #draw_image.attrib[ns('loext:mime-type')] = 'image/%s' % (img.format.lower())

            draw_subframe.append(draw_image)
    

            page_item_count +=1
            frame_no +=1
        print ('done.')

if __name__ == "__main__":
    import tempfile
    import argparse


    argparser = argparse.ArgumentParser()
    argparser.add_argument('-o','--output', type=str, help='write odt to OUTPUT. Default is to write it with the same name as the book in the same folder as the book file.')
    argparser.add_argument('-c','--crop', action='store_const', const=True,
                           help='Crop the images stored in the zip file, rather than just zoom and soft crop them. Leaves original image files alone.')
    argparser.add_argument('-b','--booksmart-dir', type=str,
                           help='Path to the BookSmart3 program directory. Required to render decorative text-box borders (the ornament images live encrypted under resources/themes/library). If omitted, borders are skipped.')

    argparser.add_argument('book_file', type=str, help='book file to convert.')

    args = argparser.parse_args()

    bookfile = args.book_file
    bookpath = os.path.dirname(os.path.abspath(bookfile))

    if args.output:
        odffile = args.output
    else:
        odffile = os.path.splitext(bookfile)[0] + '.odt'

    print (odffile)

    bs = bookxml.BookXML(bookfile)
    bodf = ezodf.newdoc('odt', odffile)

    print ("Converting %s\n        to %s." % (bookfile, odffile))

    print (bs.info['booktitle'])
    print (bs.info['subtitle'])
    print (bs.info['authorname'])
    print ('Width: %d, Height: %d' % (bs.width, bs.height))
    print ('Pages: %d' % len(bs.pages))

    with  tempfile.TemporaryDirectory(prefix='bookxml') as tempdir:
        setup_odt(bodf, bs)
        process_odt_pages(bodf, bs, tempdir=tempdir, crop_images=args.crop,
                          booksmart_dir=args.booksmart_dir)
        #process_odt_pages(bodf, bs)
        print ('Saving odt file. May take a few minutes to store all the images.')
        bodf.save()

