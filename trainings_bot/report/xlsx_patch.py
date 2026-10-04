"""Lossless cell-level patching of an .xlsx by direct XML surgery.

openpyxl cannot round-trip this workbook: it drops the embedded OLE objects,
EMF previews, printer settings and VML that the club's form is built from.
So we never parse the workbook - we rewrite individual <c> elements in the
sheet XML as text and copy every other zip entry through byte-for-byte.
"""
import re
import zipfile


def _cell_re(ref):
    return re.compile(r'<c r="%s"(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</c>)' % ref, re.S)


def find_cell(xml, ref):
    return _cell_re(ref).search(xml)


def set_cell(xml, ref, inner=None, t=None):
    """Replace a cell's content, preserving its s= style index.

    inner=None clears the cell (self-closing). t is the cell type attribute
    ('inlineStr', 's', ...) or None for numeric/formula cells.
    """
    m = find_cell(xml, ref)
    if m is None:
        raise KeyError("cell %s not present in sheet" % ref)
    style = re.search(r'\ss="(\d+)"', m.group("attrs"))
    attrs = ' r="%s"' % ref
    if style:
        attrs += ' s="%s"' % style.group(1)
    if t:
        attrs += ' t="%s"' % t
    new = "<c%s/>" % attrs if inner is None else "<c%s>%s</c>" % (attrs, inner)
    return xml[: m.start()] + new + xml[m.end() :]


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def set_number(xml, ref, value):
    return set_cell(xml, ref, "<v>%s</v>" % value)


def set_text(xml, ref, value):
    """Write a string as an inline string, so sharedStrings.xml is never touched."""
    return set_cell(xml, ref, "<is><t>%s</t></is>" % esc(value), t="inlineStr")


def set_formula(xml, ref, formula):
    """Write a formula with no cached value, forcing a recalculation on load."""
    return set_cell(xml, ref, "<f>%s</f>" % esc(formula))


def clear(xml, ref):
    return set_cell(xml, ref, None)


def rewrite(src, dst, patches, drop=()):
    """Copy an xlsx, replacing the named parts and dropping the named entries.

    patches maps a zip entry name to a callable taking and returning the
    decoded XML text.
    """
    with zipfile.ZipFile(src) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            if info.filename in drop:
                continue
            fn = patches.get(info.filename)
            if fn is not None:
                data = fn(data.decode("utf-8")).encode("utf-8")
            zout.writestr(info, data)
