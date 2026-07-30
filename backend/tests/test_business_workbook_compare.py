from __future__ import annotations

from base64 import b64decode
import json
from pathlib import Path
import subprocess
import sys

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


def test_compare_cli_writes_json_and_returns_equivalence_exit_code(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "compare_business_workbooks.py"
    expected = workbook_with_rows(
        tmp_path / "expected.xlsx", [["A", "红", "", "40", 1, "", "A 红 40"]]
    )
    equal = workbook_with_rows(
        tmp_path / "equal.xlsx", [["A", "红", "", "40", 1, "", "A 红 40"]]
    )
    different = workbook_with_rows(tmp_path / "different.xlsx", [])
    output = tmp_path / "comparison.json"

    equal_result = subprocess.run(
        [sys.executable, str(script), str(expected), str(equal), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    different_result = subprocess.run(
        [sys.executable, str(script), str(expected), str(different)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert equal_result.returncode == 0
    assert json.loads(equal_result.stdout)["equivalent"] is True
    assert json.loads(output.read_text(encoding="utf-8"))["equivalent"] is True
    assert different_result.returncode == 1
    assert json.loads(different_result.stdout)["equivalent"] is False
