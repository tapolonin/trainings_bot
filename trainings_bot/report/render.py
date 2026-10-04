"""Render a month of trainings as the single PDF the club expects.

Entries beyond the form's 19 rows continue onto a further copy of the form,
so every page carries the full header and footer. The static
Bearbeitungshinweise page is appended once, at the end.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from .fill import MONTHS_DE, chunks, fill

SOFFICE = "soffice"
# German number and date conventions, as the club requires. LibreOffice
# carries its own locale data, so the OS need not have de_DE generated.
LOCALE = "de_DE.UTF-8"
ASSETS = Path(__file__).parents[2] / "assets"
TEMPLATE = ASSETS / "template.xlsx"
INSTRUCTIONS = ASSETS / "instructions.pdf"


def pdf_name(year, month, first_name, last_name):
    """Report naming schema: YYYY-MM Lastname, Firstname.pdf."""
    return "%04d-%02d %s, %s.pdf" % (year, month, last_name, first_name)


def to_pdf(xlsx, outdir):
    """Convert one filled sheet, pinning whatever could vary between hosts.

    The locale fixes the decimal and date separators, which the club's form
    inherits from the rendering machine rather than from the file. The private
    user profile keeps the run independent of any other LibreOffice instance.
    """
    env = dict(os.environ, LC_ALL=LOCALE, LANG=LOCALE)
    subprocess.run(
        [SOFFICE, "-env:UserInstallation=file://%s" % (outdir / "loprofile"),
         "--headless", "--norestore", "--nolockcheck", "--convert-to", "pdf",
         "--outdir", str(outdir), str(xlsx)],
        check=True, capture_output=True, timeout=180, env=env,
    )
    return outdir / (xlsx.stem + ".pdf")


def totals(entries, rate):
    hours = sum((e["end"].hour * 60 + e["end"].minute
                 - e["start"].hour * 60 - e["start"].minute) / 60 for e in entries)
    return hours, hours * rate


def render(dst_dir, *, year, month, entries, instructions=True, **profile):
    """Produce the month's PDF and return its path plus the totals."""
    from pypdf import PdfWriter

    pages = chunks(entries)
    writer = PdfWriter()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for index, page_entries in enumerate(pages, start=1):
            xlsx = tmp / ("sheet%d.xlsx" % index)
            fill(TEMPLATE, xlsx, year=year, month=month,
                 entries=page_entries, **profile)
            writer.append(to_pdf(xlsx, tmp))
        if instructions:
            writer.append(INSTRUCTIONS)

        dst = Path(dst_dir) / pdf_name(year, month,
                                       profile["first_name"], profile["last_name"])
        with open(dst, "wb") as fh:
            writer.write(fh)

    hours, amount = totals(entries, profile["rate"])
    return dst, {
        "month": "%s %d" % (MONTHS_DE[month - 1], year),
        "trainings": len(entries),
        "hours": hours,
        "amount": amount,
        "form_pages": len(pages),
    }
