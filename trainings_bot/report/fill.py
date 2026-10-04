"""Fill the club's Abrechnung form from a month of training entries."""
from datetime import date

from .xlsx_patch import clear, rewrite, set_number, set_text

FIRST_ROW, LAST_ROW = 14, 32
ROWS_PER_SHEET = LAST_ROW - FIRST_ROW + 1

EPOCH = date(1899, 12, 30)
MONTHS_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def serial(day):
    return (day - EPOCH).days


def fraction(t):
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400.0


def fill(template, dst, *, first_name, last_name, sparte, iban, rate,
         year, month, entries):
    """Write one Abrechnung sheet. entries must fit in ROWS_PER_SHEET."""
    if len(entries) > ROWS_PER_SHEET:
        raise ValueError("%d entries exceed the %d rows on the form"
                         % (len(entries), ROWS_PER_SHEET))

    def patch(xml):
        xml = set_text(xml, "C4", "%s %s" % (first_name, last_name))
        xml = set_text(xml, "C6", "%s %d" % (MONTHS_DE[month - 1], year))
        xml = set_text(xml, "C8", sparte)
        xml = set_text(xml, "C10", iban)
        xml = set_number(xml, "G10", rate)

        for offset, e in enumerate(entries):
            row = FIRST_ROW + offset
            xml = set_number(xml, "A%d" % row, serial(e["date"]))
            xml = set_text(xml, "B%d" % row, e["gym"])
            xml = set_number(xml, "D%d" % row, repr(fraction(e["start"])))
            xml = set_number(xml, "E%d" % row, repr(fraction(e["end"])))
            if e.get("participants") is None:
                xml = clear(xml, "H%d" % row)
            else:
                xml = set_number(xml, "H%d" % row, e["participants"])
        return xml

    rewrite(template, dst, patches={"xl/worksheets/sheet1.xml": patch})
    return dst


def chunks(entries):
    """Split a month into as many form sheets as it needs."""
    return [entries[i:i + ROWS_PER_SHEET]
            for i in range(0, max(len(entries), 1), ROWS_PER_SHEET)] or [[]]
