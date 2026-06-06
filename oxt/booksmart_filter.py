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

# The component runs in LibreOffice's bundled Python with no console, so on
# failure log to a fixed, predictable path (not the temp dir -- LibreOffice
# points TMPDIR at its own location) so an import error is diagnosable.
_LOG = os.path.expanduser("~/booksmart_filter.log")


def _log(msg):
    try:
        with open(_LOG, "a") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


import uno
import unohelper
from com.sun.star.beans import PropertyValue
from com.sun.star.document import (XFilter, XImporter,
                                   XExtendedFilterDetection)
from com.sun.star.lang import XServiceInfo
# The helper modules ship next to this component inside the extension; make the
# extension directory importable so the imports below resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookxml
import unobuild

IMPL_NAME = "org.booksmart2odf.WriterImportFilter"
TYPE_NAME = "booksmart_Book"


def _looks_like_book(head):
    """Sniff the start of a file for the BookSmart .book signature: an XML
    <Book ...> root carrying a bookGuid attribute."""
    return b"<Book " in head and b"bookGuid=" in head


class WriterImportFilter(unohelper.Base, XFilter, XImporter,
                         XExtendedFilterDetection, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.target = None

    # --- XExtendedFilterDetection: claim .book files by content, so LO routes
    # them to us instead of treating them as generic XML ---
    def detect(self, descriptor):
        try:
            url = None
            for prop in descriptor:
                if prop.Name == "URL":
                    url = prop.Value
            if not url:
                return "", descriptor
            with open(unohelper.fileUrlToSystemPath(url), "rb") as fh:
                head = fh.read(4096)
            if not _looks_like_book(head):
                return "", descriptor
            found = False
            for prop in descriptor:
                if prop.Name == "TypeName":
                    prop.Value = TYPE_NAME
                    found = True
            if found:
                result = descriptor
            else:
                p = PropertyValue()
                p.Name = "TypeName"
                p.Value = TYPE_NAME
                result = tuple(descriptor) + (p,)
            return TYPE_NAME, result
        except Exception:
            _log("detect failed:\n" + traceback.format_exc())
            return "", descriptor

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
            # Suppress view/layout updates while we bulk-build the model; during
            # a live GUI import the layout engine reacting to each change is slow
            # and can crash the office.
            self.target.lockControllers()
            try:
                unobuild.inject_book(backend, bs)
            finally:
                self.target.unlockControllers()
            return True
        except Exception:
            _log("import failed:\n" + traceback.format_exc())
            return False

    def cancel(self):
        pass

    # --- XServiceInfo ---
    def getImplementationName(self):
        return IMPL_NAME

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getSupportedServiceNames(self):
        return ("com.sun.star.document.ImportFilter",
                "com.sun.star.document.ExtendedTypeDetection")


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    WriterImportFilter, IMPL_NAME,
    ("com.sun.star.document.ImportFilter",
     "com.sun.star.document.ExtendedTypeDetection"))
