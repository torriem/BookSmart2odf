"""Decrypt and measure BookSmart `.bev` theme assets.

`.bev` files in BookSmart's theme library are single images encrypted with
DES (ECB, PKCS5 padding) using the key `blurbboo`.  The decorative border
("frame") assets decrypt to SVG.  See BEV_FORMAT.md for the full write-up.
"""

import xml.etree.ElementTree as ET
from Crypto.Cipher import DES

# DESKeySpec("blurbbooks") uses only the first 8 bytes -> "blurbboo"
_KEY = b'blurbboo'


def decrypt_bev(path):
    """Decrypt a .bev file, returning the plaintext image bytes."""
    data = open(path, 'rb').read()
    out = DES.new(_KEY, DES.MODE_ECB).decrypt(data)

    # strip PKCS5 padding
    pad = out[-1]
    if isinstance(pad, str):  # pycrypto on py2 returns a str
        pad = ord(pad)
    if 1 <= pad <= 8:
        out = out[:-pad]
    return out


def _strip_unit(value):
    """Turn an SVG length like '45.848px' or '28' into a float."""
    value = value.strip()
    for unit in ('px', 'pt', 'in', 'cm', 'mm'):
        if value.endswith(unit):
            value = value[:-len(unit)]
            break
    return float(value)


def svg_dimensions(svg_bytes):
    """Return (width, height) of an SVG in points.

    BookSmart treats SVG user units as points, so the root width/height (or
    the viewBox extents as a fallback) map directly to points.
    """
    root = ET.fromstring(svg_bytes)

    width = root.get('width')
    height = root.get('height')
    if width is not None and height is not None:
        return (_strip_unit(width), _strip_unit(height))

    viewbox = root.get('viewBox')
    if viewbox:
        _, _, w, h = viewbox.replace(',', ' ').split()
        return (float(w), float(h))

    raise ValueError('SVG has no width/height or viewBox')


_SVG_NS = 'http://www.w3.org/2000/svg'
_XLINK_NS = 'http://www.w3.org/1999/xlink'

# Serialize SVG with its conventional prefixes (default xmlns for SVG, "xlink"
# for hrefs) instead of ElementTree's auto-generated ns0/ns1.  register_namespace
# is process-global, but this module is the only place that emits SVG.
ET.register_namespace('', _SVG_NS)
ET.register_namespace('xlink', _XLINK_NS)


def flip_svg_vertical(svg_bytes):
    """Return a copy of the SVG flipped across its horizontal axis (top<->bottom).

    LibreOffice does not reliably honor style:mirror on embedded SVG images, so
    we bake the flip into the SVG itself by wrapping all content in a group with
    a vertical-flip transform.
    """
    root = ET.fromstring(svg_bytes)

    viewbox = root.get('viewBox')
    if viewbox:
        height = float(viewbox.replace(',', ' ').split()[3])
    else:
        height = _strip_unit(root.get('height'))

    group = ET.Element('{%s}g' % _SVG_NS,
                       {'transform': 'matrix(1,0,0,-1,0,%g)' % height})
    for child in list(root):
        root.remove(child)   # ET append does not reparent, so detach first
        group.append(child)
    root.append(group)

    return ET.tostring(root, xml_declaration=True, encoding='utf-8')
