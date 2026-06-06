"""LibreOffice import filter for BookSmart .book files -> Writer (ODT).

A passive Python UNO component.  LibreOffice detects a .book file (see
Types.xcu / Filters.xcu), creates an empty Writer document, then calls
``setTargetDocument()`` and ``filter()``; we parse the .book with the
BookSmart2odf parser and inject it into that document via the UNO Writer
backend -- the combined cover spread as page 1, then the body pages.

No Pillow / lxml / exiftool are needed: the parser's image probe is swapped for
a GraphicProvider-based one, and images load through GraphicProvider.  The
helper modules (bookxml.py, unobuild.py) ship alongside this file in the .oxt.

Decorative text-box borders are intentionally not rendered here: they need the
encrypted .bev assets from a BookSmart3 install, which an import filter has no
reliable way to locate (booksmart_dir is left None).
"""

import os
import sys
import traceback

import uno
import unohelper
from com.sun.star.document import XFilter, XImporter
from com.sun.star.lang import XServiceInfo

# The helper modules ship next to this component inside the extension; make the
# extension directory importable so `import bookxml` / `unobuild` resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookxml
import unobuild

IMPL_NAME = "org.booksmart2odf.WriterImportFilter"


class WriterImportFilter(unohelper.Base, XFilter, XImporter, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.target = None

    # --- XImporter: framework hands us the empty Writer model first ---
    def setTargetDocument(self, doc):
        self.target = doc

    # --- XFilter: then asks us to fill it from the source media ---
    def filter(self, descriptor):
        url = None
        for prop in descriptor:
            if prop.Name == "URL":
                url = prop.Value
                break
        if not url or self.target is None:
            return False
        try:
            path = unohelper.fileUrlToSystemPath(url)
            # Pillow-free image probe; images load via GraphicProvider.
            bookxml.probe_image = unobuild.make_uno_prober(self.smgr, self.ctx)
            bs = bookxml.BookXML(path)
            backend = unobuild.WriterBackend(self.target, self.smgr, self.ctx)
            unobuild.inject_book(backend, bs)
            return True
        except Exception:
            traceback.print_exc()
            return False

    def cancel(self):
        pass

    # --- XServiceInfo ---
    def getImplementationName(self):
        return IMPL_NAME

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getSupportedServiceNames(self):
        return ("com.sun.star.document.ImportFilter",)


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    WriterImportFilter, IMPL_NAME, ("com.sun.star.document.ImportFilter",))
