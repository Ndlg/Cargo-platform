from pathlib import Path
from typing import Iterable

from openpyxl import Workbook


def export_rows_to_xlsx(headers: list[str], rows: Iterable[dict[str, object]], target: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return target
