"""Generate original synthetic fixtures; never overwrite real report assets."""
from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfWriter


def create(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    template = folder / 'template.xlsx'
    instructions = folder / 'instructions.pdf'
    if template.exists() or instructions.exists():
        raise SystemExit('Refusing to overwrite existing assets. Use a clean checkout.')
    workbook = Workbook()
    sheet = workbook.active
    sheet['A1'] = 'Synthetic training report — CI fixture'
    for ref in ('C4', 'C6', 'C8', 'C10', 'G10'):
        sheet[ref] = 'placeholder'
    for row in range(14, 33):
        for column in 'ABDEH':
            sheet[f'{column}{row}'] = 0
        sheet[f'F{row}'] = f'=(E{row}-D{row})*24'
        sheet[f'G{row}'] = f'=F{row}*$G$10'
        sheet[f'G{row}'].number_format = '[$-407]#,##0.00'
    sheet['F33'] = '=SUM(F14:F32)'
    sheet['G33'] = '=SUM(G14:G32)'
    sheet['G33'].number_format = '[$-407]#,##0.00'
    sheet.column_dimensions['B'].width = 25
    sheet.column_dimensions['C'].width = 25
    sheet.print_area = 'A1:H34'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    workbook.save(template)
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(instructions)


if __name__ == '__main__':
    import sys
    create(sys.argv[1] if len(sys.argv) > 1 else 'assets')
