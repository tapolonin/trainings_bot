"""Read training entries back out of a filled workbook.

Used to turn the club's real months into test fixtures, and to verify that
what we write is what lands in the file.
"""
import re
import zipfile
from datetime import date, time, timedelta
from xml.etree import ElementTree as ET

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FIRST_ROW, LAST_ROW = 14, 32
EPOCH = date(1899, 12, 30)


def _q(tag):
    return "{%s}%s" % (M, tag)


def _to_time(fraction):
    total = int(round(float(fraction) * 86400))
    return time(total // 3600, total % 3600 // 60, total % 60)


def read_cells(path, sheet="xl/worksheets/sheet1.xml"):
    """Map every populated cell reference to its value, resolving strings."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(_q("t")))
                      for si in root.findall(_q("si"))]
        root = ET.fromstring(z.read(sheet))

    cells = {}
    for c in root.iter(_q("c")):
        kind = c.get("t")
        if kind == "inlineStr":
            node = c.find(_q("is"))
            cells[c.get("r")] = "".join(t.text or "" for t in node.iter(_q("t")))
            continue
        v = c.find(_q("v"))
        if v is None:
            continue
        cells[c.get("r")] = shared[int(v.text)] if kind == "s" else v.text
    return cells


def read_entries(path):
    cells = read_cells(path)
    entries = []
    for row in range(FIRST_ROW, LAST_ROW + 1):
        day = cells.get("A%d" % row)
        if day is None:
            continue
        participants = cells.get("H%d" % row)
        entries.append({
            "date": EPOCH + timedelta(days=int(float(day))),
            "gym": cells.get("B%d" % row),
            "start": _to_time(cells["D%d" % row]),
            "end": _to_time(cells["E%d" % row]),
            "participants": int(participants) if participants else None,
        })
    return entries
