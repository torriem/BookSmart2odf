import os
import lxml.etree
import PIL.Image
import tempfile
import subprocess

class TextBox(object):
    def __init__(self, xmlre = None):
        if xmlre:
            self.x, self.y, self.width, self.height = [ int(i) for i in xmlre.split(',') ]
        else:
            self.x = 0
            self.y = 0
            self.width = 0
            self.height = 0

        self.paragraphs = []
        self.border = None # a Border object, if this text box has a decorative border

    def __repr__(self):
        return ('TextBox(%d,%d,%d,%d,%s)' % (self.x, self.y, self.width, self.height, repr(self.paragraphs)))

    def __str__(self):
        return repr(self)

class Border(object):
    """A decorative border (BorderDefinition) attached to a text box.

    The border places one or more ornament images (SVGs from the theme
    library) along the edges of the text box.  See BEV_FORMAT.md and the
    BorderDefinition rendering notes for details.
    """

    # mirrorEdge values (from BorderDefinition.class constants)
    MIRROR_OFF = 0      # only the declared edges are drawn
    MIRROR_ALL = 1      # declared edge mirrored/rotated to all four sides
    MIRROR_OPPOSITE = 2 # declared edge mirrored to the opposite side

    def __init__(self, xmlelement):
        self.inset = int(xmlelement.get('inset', '0'))
        self.color = '#%s' % xmlelement.get('color', '#ff000000')[-6:]
        self.tile_edges = xmlelement.get('tileEdges', 'false') == 'true'
        self.mirror_edge = int(xmlelement.get('mirrorEdge', '0'))

        # location -> image stem (without the .svg extension)
        self.edges = {}
        for edge in xmlelement.findall('edge'):
            location = edge.get('location')
            image = edge.get('image', '')
            if location and image:
                self.edges[location] = os.path.splitext(image)[0]

    def __repr__(self):
        return 'Border(inset=%d, color=%s, tile=%s, mirror=%d, edges=%s)' % (
            self.inset, self.color, self.tile_edges, self.mirror_edge, self.edges)

    def __str__(self):
        return repr(self)

class CoverPart(object):
    """One part of a book cover (front flap, front cover, spine, etc.).

    Each part has its own dimensions and background, and holds text boxes and
    images parsed exactly like a body page.
    """

    def __init__(self, title, width, height, bgcolor):
        self.title = title
        self.width = width    # in points
        self.height = height
        self.bgcolor = bgcolor
        self.text_boxes = []
        self.images = []

    def __repr__(self):
        return 'CoverPart(%s, %dx%d, %s)' % (self.title, self.width,
                                             self.height, self.bgcolor)

    def __str__(self):
        return repr(self)

class ImageBox(object):
    OVERWRITE = 1
    SAVEASCOPY = 2

    def __init__(self, filepath):
        self.filename = filepath
        image = PIL.Image.open(filepath)
        if 'dpi' in image.info:
            self.dpi = image.info['dpi']
        else:
            self.dpi = (72, 72)

        self.format = image.format.lower()
        self.img_size = image.size
        self.box_x = 0
        self.box_y = 0
        self.width = 0
        self.height = 0

        self.vflip = False
        self.hflip = False
        self.zoom = 1.0
        self.crop_left = 0.0 # inches
        self.crop_right = 0.0
        self.crop_top = 0.0
        self.crop_bottom = 0.0
        self.x = 0.0
        self.y = 0.0

    def fix_dpi(self, save_disk = None, **kwargs):
        """
            fix the embedded image file DPI if necessary. 

            If save_disk is OVERWRITE, set the DPI on the file in place,
            setting it permanently.

            If save_disk is SAVEASCOPY, create a modified image file in
            the same directory as the original image.  Useful if the final
            ODF document is linking to the images rather than embedding them.

            If save_disk is None, create a temporary file where the image is
            copied and the DPI set.

            If tempdir is passed as a kwarg, a temporary file will be created
            within that directory if we're not using SAVEASCOPY or OVERWRITE
        """

        if self.dpi[0] != 300 and self.dpi[0] != 600:
            # If DPI seems odd, LibreOffice often has problems with the dpi and
            # any cropping we do to the image will be wrong.  So call out to
            # exiftool externally to change the DPI to a default of 300.

            if not save_disk:
                if 'tempdir' in kwargs:
                    if not os.path.exists(kwargs['tempdir']):
                        os.mkdir(kwargs['tempdir'])

                    newfile = os.path.join(kwargs['tempdir'],os.path.basename(os.path.abspath(self.filename)))+'.%s' % self.format
                    #print (newfile)
                    with open(newfile,'wb') as newfileobj:
                        newfileobj.write(open(self.filename,'rb').read())
                else:
                    newfileobj = tempfile.NamedTemporaryFile(delete=False, prefix='bookxml', suffix='.%s' % self.format)
                    newfileobj.write(open(self.filename,'rb').read())
                    newfile = newfileobj.name
                    newfileobj.close()
            elif save_disk == ImageBox.SAVEASCOPY:
                newfile = self.filename + ".300dpi.%s" % self.format
                if os.path.exists(newfile):
                    self.filename = newfile
                    self.dpi = (300, 300)
                    return

                with open(newfile,'wb') as newfileobj:
                    newfileobj.write(open(self.filename,'rb').read())
            else:
                newfile = self.filename

            exiftool = kwargs.get('exiftool','/usr/bin/exiftool')

            # call exiftool to set the DPI to 300:
            subprocess.run([exiftool, '-jfif:Xresolution=300', '-jfif:Yresolution=300', '-Xresolution=300', '-Yresolution=300', newfile], capture_output = True)

            if not save_disk == ImageBox.OVERWRITE:
                # delete exiftool's backup
                os.unlink("%s_original" % newfile)
            self.filename = newfile
            self.dpi = (300,300)

    def calculate_crop(self):
        """
            Converts the BookSmart style of image zooming and placement into
            some crop measurements that the ODF formats use.  DPI of the image
            file must be set correctly for this to work.
        """

        img_aspect = self.img_size[0] / self.img_size[1]
        box_aspect = self.width / self.height

        # calculate pix per point at 100% scale (smallest side scaled to box)
        # Booksmart transformation numbers are all in points based on the
        # box size, so we have to figure out what that means in pixels so we
        # can compute the necessary clipping box in pixels.

        if img_aspect >= 1 and box_aspect >= 1:
            # aspects both >= 1

            if box_aspect < img_aspect:
                # case 1, box aspect < image aspect: scale image y
                pixperpt = self.img_size[1] / self.height
            else:
                # case 2, box aspect > image aspect: scale image x
                pixperpt = self.img_size[0] / self.width
        elif img_aspect < 1 and box_aspect < 1:
            # aspects both < 1
            if box_aspect > img_aspect:
                # case 1, box aspect > image aspect: scale image x
                pixperpt = self.img_size[0] / self.width
            else:
                # case 2, box aspect < image aspect: scale image y
                pixperpt = self.img_size[1] / self.height
        elif box_aspect >=1 and img_aspect <1:
                # box aspect >= 1, image aspect < 1: scale image x
                pixperpt = self.img_size[0] / self.width
        else:
                # box aspect < 1, image aspect >= 1: scale image y
                pixperpt = self.img_size[1] / self.height

        # calculate how much of the image will be shown as a percentage
        clip_pixperpt = pixperpt / self.zoom

        # enlarge image dimensions by zoom.
        width = self.img_size[0] / clip_pixperpt #width in points
        height = self.img_size[1] / clip_pixperpt #height in points
        crop_left = 0
        crop_right = 0
        crop_top = 0
        crop_bottom = 0

        #print ('pic size in pts: ', width,height)
        
        # if x is negative, crop on the left, set x to 0
        if self.x < 0:
            crop_left = int(abs(self.x) * clip_pixperpt) #in pixels
            width += self.x # remove left crop from width
            self.x = 0
        
        # if y is negative, crop on the top, set y to 0
        if self.y < 0:
            crop_top = int(abs(self.y) * clip_pixperpt)
            height += self.y # remove top crop from height
            self.y = 0

        if (width + self.x) > self.width: 
            # if image sticks beyond the right side of the frame
            # crop it from the right
            crop_right = width + self.x - self.width
            width -= crop_right
            crop_right = int(crop_right * clip_pixperpt)
            #print ('cropping right ', crop_right)


        if (height + self.y ) > self.height:
            # if image sticks beyond the bottom of the frame,
            # crop it from the bottom.
            crop_bottom = height + self.y - self.height
            height -= crop_bottom
            crop_bottom = int(crop_bottom * clip_pixperpt)

        # convert from pixels to inches, using the image dpi
        self.crop_top = crop_top / self.dpi[1]
        self.crop_bottom = crop_bottom / self.dpi[1]
        self.crop_left = crop_left / self.dpi[0]
        self.crop_right = crop_right / self.dpi[0]

    def crop_file(self):
        # just assume a default of 300x300
        self.dpi = (300,300)
        self.calculate_crop()

        area = (self.crop_left * 300, self.crop_top * 300,
                self.img_size[0] - self.crop_right * 300,
                self.img_size[1] - self.crop_bottom * 300)

        newfileobj = tempfile.NamedTemporaryFile(delete=False, prefix='bookxml', suffix='.%s' % self.format)
        newfile = newfileobj.name
        newfileobj.close()
        img = PIL.Image.open(self.filename)
        img.crop(area).save(newfile, dpi=(300,00))
        self.filename = newfile
        self.crop_top = 0
        self.crop_bottom = 0
        self.crop_left = 0
        self.crop_right = 0

        

    def __str__(self):
        return ' %s, %s %d %d, %d %d, %d' % (self.filename, self.format, self.dpi[0], self.box_x,
                                        self.box_y, self.width, self.height)

    def __repr__(self):
        return str(self)
            
class ParagraphStyle(object):
    keys = ['name', 'font', 'size', 'color', 'alignment', 'bold', 'italic', 'line_spacing', 'left_indent', 'underline']
    ALIGN = { 1: 'center',
              2: 'end',
              0: 'start',
              3: 'justify'}

    def __init__(self, style_dict = None):
        self.name = None

        if style_dict:
            self.font      = style_dict['font']
            self.size      = style_dict['size']
            self.color     = style_dict['color']
            self.alignment = style_dict['align']
            self.bold      = style_dict['bold']
            self.italic    = style_dict['italic']
            self.italic    = style_dict['underline']
            self.line_spacing = style_dict.get('line_spacing',0.0)
            self.left_indent = style_dict.get('left_indent',0.0)
        else:
            self.font      = None
            self.size      = None
            self.color     = None
            self.alignment = None
            self.bold      = None
            self.italic    = None
            self.underline    = None
            self.line_spacing = 0.0
            self.left_indent = 0.0

    def __str__(self):
        return "name=%s, font=%s, size=%s, color=%s, alignment=%d, bold=%s, italic=%s, underline=%s, line_spacing=%f, left_indet=%f" % (
             self.name, self.font, self.size, self.color, self.alignment, self.bold, self.italic, self.underline,
             self.line_spacing, self.left_indent)

    def __repr__(self):
        return "Paragraph Style {name:%s, font:%s, size:%s, color:%s, alignment:%d, bold:%s, italic:%s, underline:%s, line_spacing=%f, left_indent=%f}" % (
                self.name, self.font, self.size, self.color, self.alignment, self.bold, self.italic, self.underline,
                self.line_spacing, self.left_indent)

    def __setitem__(self, name, value):
        if name in ParagraphStyle.keys:
            self.__dict__[name] = value
        else:
            raise KeyError

    def __getitem__(self, name):
        if name in ParagraphStyle.keys:
            return self.__dict__[name]
        else:
            raise KeyError

    def simple_serialize(self):
        s = '%s|%s|%s|%d|%s|%s|%s|%f|%f' % (self.font,
                                            self.size,
                                            self.color,
                                            self.alignment,
                                            self.bold,
                                            self.italic,
                                            self.underline,
                                            self.line_spacing,
                                            self.left_indent)

        return s

class Paragraph(object):
    def __init__(self):
        self.style = None # a string name referring to a style
        self.spans = [] # list of Span objects

    def __repr__(self):
        return 'Paragraph(%s, %s)' % (self.style, repr(self.spans))

    def __str__(self):
        return repr(self)
    
class Span(object):
    def __init__(self):
        self.style = None # string name of span style
        self.text = None
        self.variable = None

    def __repr__(self):
        return 'Span(%s, "%s", %s)' % (self.style, self.text, self.variable)

    def __str(self):
        return repr(self)

class SpanStyle(object):
    keys = ['name', 'font', 'size', 'color', 'bold', 'italic', 'underline']

    def __init__(self, style_dict = None):
        self.name = None

        if style_dict:
            self.font = style_dict.get('font', None)
            self.size = style_dict.get('size', None)
            self.color = style_dict.get('color', None)
            self.bold = style_dict.get('bold', None)
            self.italic = style_dict.get('italic', None)
            self.underline = style_dict.get('underline', None)
            #self.variable = style_dict.get('variable', None)
        else:
            self.font = None
            self.size = None
            self.color = None
            self.bold = None
            self.italic = None
            self.underline = None
            #self.variable = None

    def simple_serialize(self):
        s = '%s|%s|%s|%s|%s|%s' % (self.font,
                                   self.size,
                                   self.color,
                                   self.bold,
                                   self.italic,
                                   self.underline)

        return s

    def __str__(self):
        return "name=%s, font=%s, size=%s, color=%s, bold=%s, italic=%s, underline=%s" % (
             self.name, self.font, self.size, self.color, self.bold, self.italic, self.underline)

    def __repr__(self):
        return "Span Style {name:%s, font:%s, size:%s, color:%s, bold:%s, italic:%s, underline:%s}" % (
                self.name, self.font, self.size, self.color, self.bold, self.italic, self.underline)

    def __setitem__(self, name, value):
        if name in SpanStyle.keys:
            self.__dict__[name] = value
        else:
            raise KeyError

    def __getitem__(self, name):
        if name in SpanStyle.keys:
            return self.__dict__[name]
        else:
            raise KeyError

class PageStyle(object):
    keys = ['name', 'bgcolor']

    def __init__(self, xmlattribs = None):
        if xmlattribs:
            self.name = xmlattribs['id']
            self.bgcolor = '#%s' % xmlattribs['color'][-6:]
        else:
            self.name = None
            self.bgcolor = None

    def __repr__(self):
        return 'PageStyle(name:%s, bgcolor:%s)' % (
              self.name, self.bgcolor)

    def __str__(self):
        return repr(self)

    def simple_serialize(self):
        s = '%s|%s' % (self.name,
                       self.bgcolor)
        return s


    def __setitem__(self, name, value):
        if name in PageStyle.keys:
            self.__dict__[name] = value
        else:
            raise KeyError

    def __getitem__(self, name):
        if name in PageStyle.keys:
            return self.__dict__[name]
        else:
            raise KeyError

class javaxml_exception(Exception):
    pass

def javaxml_to_python(object_):
    current_object = None
    if object_.attrib["class"] == "java.util.LinkedList":
        #print ("Creating a new list")
        current_object = []

        for operation in object_: # list contents
            #print ("operation is: ", operation.name, operation["method"])
            if operation.attrib["method"] == "add": # object, method = add
                #print ("adding to list:")
                for to_add in operation: # what will we add?
                    if to_add.tag == "object": 
                        current_object.append(javaxml_to_python(to_add))
                    elif to_add.tag == "string":
                        #print ("added string")
                        current_object.append(to_add.text)
                    elif to_add.tag == "null":
                        #print ("added null")
                        current_object.append(None)
                    else:
                        raise javaxml_exception("unrecognized list item %s %s=>%s" % (operation['method'], to_add.tag, to_add.text))
            else:
                raise javaxml_exception("unrecognized list operation %s" % operation['method'])


    elif object_.attrib["class"] == "java.util.HashMap":
        #print ("Creating a new dict")
        current_object = {}
        for operation in object_: # dict contents
            #print ("operation is: ", operation.name, operation["method"])
            if operation.attrib["method"] == "put": # object, method = put
                #print ("adding to dict:")

                # First will always be a string key, second will be a value
                key = None
                for to_add in operation: # what will we add?
                    if not key: 
                        key = to_add.text
                        continue

                    if to_add.tag == "object":

                        if 'class' in to_add.attrib:
                            current_object[key] = javaxml_to_python(to_add)

                        elif 'idref' in to_add.attrib:
                            current_object[key] = to_add.attrib["idref"]

                    elif to_add.tag == "string":
                        current_object[key] = (to_add.text)
                    elif to_add.tag == "null":
                        current_object[key] = None
                    elif to_add.tag == "int":
                        current_object[key] = int(to_add.text)
                    elif to_add.tag == 'float':
                        current_object[key] = float(to_add.text)
                    elif to_add.tag == "boolean":
                        current_object[key] = to_add.text #TODO convert to real boolean
                    else:
                        raise javaxml_exception("unrecognized dict value %s %s" % (to_add.tag, to_add.text) )
                    break # we only expect two xml elements in a hashmap, and we now have both
            else:
                raise javaxml_exception("unrecognized dict operation %s" % operation['method'])

    elif object_.attrib["class"] == "java.awt.Color":
        current_object = {}
        if 'id' in object_.attrib:
            current_object["color_id"] = object_.attrib['id']

        red = None
        green = None
        blue = None
        alpha = None

        for i in object_:
            if red is None:
                red = int(i.text)
            elif green is None:
                green = int(i.text)
            elif blue is None:
                blue = int(i.text)
            elif alpha is None:
                alpha = int(i.text)
    
        current_object['color'] = (red, green, blue, alpha) 
    else:
        raise javaxml_exception("unimplemented object class %s" % _object["class"])


    return current_object
       

class BookXML(object):

    def __init__(self, book_file):
        tree = lxml.etree.parse(open(book_file, 'r'))
        self.book = tree.getroot()
        self.book_path = os.path.dirname(book_file)
        self.info = {}
        for book_var in self.book.findall('bookVar'):
            self.info[book_var.attrib['name'][1:].lower()] = book_var.attrib['value']

        self.width = float(self.book.attrib['width']) # in points
        self.height = float(self.book.attrib['height'])

        self.path = os.path.dirname(os.path.abspath(book_file))

        self.pages = []
        self.page_info = {}
        self.text_boxes = {}
        self.images = {}

        self._styles = {}  # BookSmart TextStyleDefinitions
        self._color_cache = {} # BookSmart color definitions
        self._paragraph_style_cache = {}
        self._pgs_no = 0
        self._span_style_cache = {}
        self._ss_no = 0
        self._page_style_cache = {}

        self.fonts = []
        self.cover = None       # list of CoverPart, or None if no cover
        self.cover_type = None  # 'hard' or 'soft'

        self.read_book_styles()
        self.read_pages()
        self.read_cover()


        
    def get_paragraph_styles(self):
        """
            Return a list of paragraph style objects representing the
            unique paragraph styles in this book.
        """

        return [ self._paragraph_style_cache[item] for item in self._paragraph_style_cache ]

    def get_span_styles(self):
        """ 
            Return a list of span style objects representing the unique
            span styles used in this book.
        """

        return [ self._span_style_cache[item] for item in self._span_style_cache ]

    def get_page_styles(self):
        """
            Return a list of page style objects representing the unique
            page styles used in this book. Currently this only refers
            to the background color.
        """
        
        return [ self._page_style_cache[item] for item in self._page_style_cache ]

    def read_book_styles(self):
        style_defs = self.book.findall('TextStyleDefinition')

        for s in style_defs:
            sid = s.attrib['id'].lower()
            self._styles[sid] = { 'align': 0,
                                  'bold' : False,
                                  'italic' : False,
                                  'underline': False,
                                  'line_spacing': 0.0,
                                  'left_indent': 0.0
                                  }
            for key in s.attrib:
                if key in ['font', 'size']:
                    self._styles[sid][key] = s.attrib[key]
                if key == 'color':
                    self._styles[sid][key] = '#%s' % s.attrib[key][-6:]
                if key == 'align':
                    self._styles[sid][key] = int(s.attrib[key])

                if key == 'italic':
                    if s.attrib[key] == 'true':
                        self._styles[sid][key] = True
                    else:
                        self._styles[sid][key] = False
                   
                if key == 'bold':
                    if s.attrib[key] == 'true':
                        self._styles[sid][key] = True
                    else:
                        self._styles[sid][key] = False

                if key == 'underline':
                    if s.attrib[key] == 'true':
                        self._styles[sid][key] = True
                    else:
                        self._styles[sid][key] = False


    def read_pages(self):
        pagesList = self.book.find('pagesList')
        self.book_objects = self.book.find('bookObjects')

        #pagesList = [ pagesList[3]] #debug just grab one page
        for (pageno, page) in enumerate(pagesList):
            id_ = page.attrib['id']

            self.pages.append(id_)

            pagetag = self.book_objects.findall("Page[@id='%s']" % id_)[0]
            backgrounddef = pagetag.find("BackgroundDefinition")
            #self.book_objects.findall("Page[@id='%s']" % id_)[0][2]
            page_style = PageStyle(backgrounddef.attrib)

            if not page_style.simple_serialize() in self._page_style_cache:
                self._page_style_cache[page_style.simple_serialize()] = page_style

            self.page_info[id_] = { 'page_style': page_style.name }
            if 'pagination' in pagetag.attrib:
                self.page_info[id_]['pagination'] = pagetag.attrib['pagination']

            self.text_boxes[id_] = self._read_text_boxes(id_, pageno)
            self.images[id_] = self._read_images(id_, pageno)

    def _read_text_boxes(self, parent_id, pageno):
        """Parse all TextContent boxes for a page/cover-part into TextBox list."""
        text_boxes = []
        for tc in self.book_objects.findall("TextContent[@parentId='%s']" % parent_id):
                text_box = TextBox(tc.attrib['re'])

                coords = [ float(n) for n in tc.attrib['re'].split(',') ]

                rxt = float(tc.attrib['rxt']) # kind of like a margin but only added to even pages to offset layout
                text_box.x = coords[0] +  (rxt if not (pageno % 2) else 0)
                text_box.y = coords[1]
                text_box.width = coords[2]
                text_box.height = coords[3]

                #if rxt: print ('rxt is ', rxt)
                #print ('%d, %d x %d, %d, on page %d' % (text_box.x, text_box.y, text_box.width, text_box.height, pageno+1))


                base_style = {} # this will hold the default style going into the parsing
                lookup_style = self._styles.get(tc.attrib['ts'].lower(),{})
                for key in lookup_style:
                    base_style[key] = lookup_style[key]

                if 'color' in base_style:
                    base_style['color'] = '#%s' % base_style['color'][-6:] # trim off alpha channel

                # text is a serialized structure of java objects stored in the dm tag
                # we'll convert that to something we can parse

                dm = tc.find('dm')

                border_definition = tc.find('BorderDefinition')
                if border_definition is not None:
                    text_box.border = Border(border_definition)

                dmtext = dm.text.encode('utf-8')
                dmobj = lxml.etree.fromstring(dmtext)[0] # get the first child of the java node
                
                text_structure = javaxml_to_python(dmobj) # a nested structure of lists and dicts

                # Now to parse that structure of lists and dicts and make sense of it
                pstyle = None
                paragraph = None

                for i in text_structure:
                    if isinstance(i, dict):
                        # a pgraph style dict signifies the start of a new paragraph

                        if pstyle:

                            # paragraph was open, close it now, append it to the
                            # list we're building.  If it was empty we can just
                            # throw it away.

                            if len(paragraph.spans):
                                if not pstyle.simple_serialize() in self._paragraph_style_cache:
                                    pstyle.name = 'PS%d' % self._pgs_no
                                    self._pgs_no += 1

                                    self._paragraph_style_cache[pstyle.simple_serialize()] = pstyle
                                else:
                                    pstyle = self._paragraph_style_cache[pstyle.simple_serialize()]
                                paragraph.style = pstyle.name

                                text_box.paragraphs.append(paragraph)
                                # append to list

                        paragraph = Paragraph()
                        pstyle = ParagraphStyle(base_style)

                        if 'resolver' in i:
                            # override our default style for this paragraph 
                            lookup_style = self._styles[i['resolver'].split('.')[0].lower()]

                            for key in lookup_style:
                                if key == 'align': pstyle['alignment'] = lookup_style[key]
                                elif key != 'id':
                                    pstyle[key] = lookup_style[key]

                        if 'Alignment' in i:
                            pstyle['alignment'] = int(i['Alignment'])

                        if 'LeftIndent' in i:
                            pstyle['left_indent'] = i['LeftIndent']


                        if 'LineSpacing' in i:
                            pstyle['line_spacing'] = i['LineSpacing']

                        continue
                    
                    if not pstyle['font'] in self.fonts:
                        self.fonts.append(pstyle['font'])

                    spans_wrapper = (sp for sp in i) # list of items
                    span_style = SpanStyle()
                    text_span = Span()

                    for span in spans_wrapper:
                        if isinstance(span, dict):
                            # style the span
                            span_style = SpanStyle()

                            if 'size' in span:
                                span_style['size'] = span['size']
                            if 'family' in span:
                                span_style['font'] = span['family']
                            if 'foreground' in span:
                                if isinstance(span['foreground'], str):
                                    # if just a color-id, look it up
                                    color = self._color_cache[span['foreground']]
                                else:
                                    # otherwise parse it, save it in cache
                                    color = span['foreground']['color']

                                    if 'color_id' in span['foreground']:
                                        self._color_cache[span['foreground']['color_id']] = color

                                color_hex = '#%02x%02x%02x' % ( color[0], color[1], color[2] )
                                span_style['color'] = color_hex
                            if 'bold' in span:
                                if span['bold'].lower() == 'true':
                                    span_style['bold'] = True
                                else:
                                    span_style['bold'] = False
                            #else:
                            #    span_style['bold'] = False

                            if 'italic' in span:
                                if span['italic'].lower() == 'true':
                                    span_style['italic'] = True
                                else:
                                    span_style['italic'] = False
                            #else:
                            #    span_style['italic'] = False

                            if 'underline' in span:
                                if span['underline'].lower() == 'true':
                                    span_style['underline'] = True
                                else:
                                    span_style['underline'] = False
                            #else:
                            #    span_style['underline'] = False

                            if 'bsVar' in span:
                                text_span.variable = span['bsVar']

                            # now get the text that follows
                            span = next(spans_wrapper)
                        
                        if span: # and span.strip():
                            #print (span_style['font'])
                            if span_style['font'] and not span_style['font'] in self.fonts:
                                self.fonts.append(span_style['font'])

                            if not span_style.simple_serialize() in self._span_style_cache:
                                span_style.name = 'SS%d' % self._ss_no
                                self._ss_no += 1
                                
                                self._span_style_cache[span_style.simple_serialize()] = span_style
                            else:
                                span_style = self._span_style_cache[span_style.simple_serialize()]
                            text_span.text = span
                            text_span.style = span_style.name

                            paragraph.spans.append( text_span )
                # if the paragraph is empty, we should throw it away.

                if len(paragraph.spans):
                    if not pstyle.simple_serialize() in self._paragraph_style_cache:
                        pstyle.name = 'PS%d' % self._pgs_no
                        self._pgs_no += 1
                        self._paragraph_style_cache[pstyle.simple_serialize()] = pstyle
                    else:
                        pstyle = self._paragraph_style_cache[pstyle.simple_serialize()]

                    paragraph.style = pstyle.name
                    text_box.paragraphs.append(paragraph)
                text_boxes.append(text_box)

        return text_boxes

    def _read_images(self, parent_id, pageno):
        """Parse all ImageContent boxes for a page/cover-part into ImageBox list."""
        images = []
        for ic in self.book_objects.findall("ImageContent[@parentId='%s']" % parent_id):
                if not 'content' in ic.attrib:
                    #print ("imagecontent %s has no image." % ic.attrib['id'])
                    # empty box, no image, skip
                    continue

                try:
                    imagebox = ImageBox(os.path.join(self.path,'library',ic.attrib['content']))
                except FileNotFoundError as e:
                    try:
                        imagebox = ImageBox(os.path.join(self.path,'library','%s.original' % ic.attrib['content']))
                    except FileNotFoundError as e:
                        # print ("could not find %s" % ic.attrib['content'])
                        # skip
                        continue

                coords = [ float(n) for n in ic.attrib['re'].split(',') ]

                rxt = float(ic.attrib['rxt']) # kind of like a margin but only added to even pages
                imagebox.box_x = coords[0] + (rxt if not (pageno % 2) else 0)
                imagebox.box_y = coords[1]
                imagebox.width = coords[2]
                imagebox.height = coords[3]

                if len(ic.getchildren()):
                    transform = ic[0][0]

                    imagebox.x = float(transform.attrib['x']) # in pts
                    imagebox.y = float(transform.attrib['y'])

                    imagebox.zoom = float(transform.attrib['zoom']) / 100
                    if transform.attrib['vflip'] == 'true':
                        imagebox.vflip = True
                    else:
                        imagebox.vflip = False

                    if transform.attrib['hflip'] == 'true':
                        imagebox.hflip = True
                    else:
                        imagebox.hflip = False

                #print ("found %s on page %d" % (ic.attrib['content'], pageno+1))
                images.append(imagebox)

        return images

    def read_cover(self):
        """Parse the book cover (HardCover/SoftCover) into self.cover.

        Each referenced CoverPage becomes a CoverPart with its own dimensions,
        background, text boxes and images.  Soft covers omit the inner flaps.
        """
        cover_elem = self.book_objects.find('HardCover')
        self.cover_type = 'hard'
        if cover_elem is None:
            cover_elem = self.book_objects.find('SoftCover')
            self.cover_type = 'soft'
        if cover_elem is None:
            self.cover = None
            self.cover_type = None
            return

        parts = []
        pages = cover_elem.find('pagesList')
        for pg in pages.findall('pages'):
            cid = pg.attrib['id']
            matches = self.book_objects.findall("CoverPage[@id='%s']" % cid)
            if not matches:
                continue
            cp = matches[0]

            title = cp.attrib.get('title', '')
            if self.cover_type == 'soft' and 'Flap' in title:
                continue # soft covers have no flaps

            bgdef = cp.find('BackgroundDefinition')
            bgcolor = PageStyle(bgdef.attrib).bgcolor if bgdef is not None else '#ffffff'

            part = CoverPart(title, float(cp.attrib['w']), float(cp.attrib['h']),
                             bgcolor)
            part.text_boxes = self._read_text_boxes(cid, 0)
            part.images = self._read_images(cid, 0)
            parts.append(part)

        self.cover = parts




    def page_ids(self):
        return self.page_ids

    def page_info(self):
        pass

if __name__ == "__main__":
    import sys
    book = BookXML(sys.argv[1])


    """
    for ps in book.get_paragraph_styles():
        print (ps)

    for ss in book.get_span_styles():
        print (ss)
    print ()

    """
    print (book.info)

    """
    for page_id in book.pages:
        for image in book.images[page_id]:
            image.fix_dpi(ImageBox.OVERWRITE, tempdir='/tmp/bookxmltemp')
            image.crop_image()
            print (image.x, image.y, image.crop_top, image.crop_right, image.crop_bottom, image.crop_left)
        #print (page_id, book.images[page_id])

    #print (len(book.get_paragraph_styles()), len(book.get_span_styles()), len(book.text_boxes))
    """



