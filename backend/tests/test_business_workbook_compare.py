from __future__ import annotations

from base64 import b64decode
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image

from scripts.compare_business_workbooks import BUSINESS_HEADERS, compare_workbooks, workbook_manifest


PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL6lwAAAABJRU5ErkJggg=="
)


def workbook_with_rows(path: Path, rows: list[list[object]]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "供货单"
    sheet.append(BUSINESS_HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def workbook_with_image(path: Path, image_path: Path, anchor: str) -> Path:
    image_path.write_bytes(PNG)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "供货单"
    sheet.append(BUSINESS_HEADERS)
    sheet.add_image(Image(image_path), anchor)
    workbook.save(path)
    return path


def test_compare_preserves_duplicate_multiplicity(tmp_path: Path) -> None:
    expected = workbook_with_rows(
        tmp_path / "expected.xlsx",
        [["A", "红", "", "40", 1, "", "A 红 40"], ["A", "红", "", "40", 1, "", "A 红 40"]],
    )
    actual = workbook_with_rows(
        tmp_path / "actual.xlsx",
        [["A", "红", "", "40", 1, "", "A 红 40"]],
    )

    report = compare_workbooks(expected, actual)

    assert report["equivalent"] is False
    assert report["differences"][0]["kind"] == "row_count"


def test_compare_ignores_xlsx_zip_metadata(tmp_path: Path) -> None:
    expected = workbook_with_rows(
        tmp_path / "expected.xlsx", [["A", "红", "", "40", 1, "", "A 红 40"]]
    )
    actual = workbook_with_rows(
        tmp_path / "actual.xlsx", [["A", "红", "", "40", 1, "", "A 红 40"]]
    )

    assert compare_workbooks(expected, actual)["equivalent"] is True


def test_compare_detects_moved_images(tmp_path: Path) -> None:
    expected = workbook_with_image(tmp_path / "expected.xlsx", tmp_path / "expected.png", "A2")
    actual = workbook_with_image(tmp_path / "actual.xlsx", tmp_path / "actual.png", "B2")

    report = compare_workbooks(expected, actual)

    assert report["equivalent"] is False
    assert workbook_manifest(expected)["sheets"][0]["images"][0]["anchor"] == "A2"
