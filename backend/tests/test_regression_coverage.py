from app.services.regression_coverage import analyze_waybill_coverage


def test_waybill_coverage_distinguishes_blocking_loss_from_warnings() -> None:
    duplicate_row = {
        "raw_record_id": 10,
        "source_label": "面单 1-子1",
        "product_text": "same",
        "sales_attr1_text": "same",
        "sales_attr2_text": "same",
        "quantity_text": "1",
        "remark_text": "",
    }
    warning_rows = [
        duplicate_row,
        {**duplicate_row, "source_label": "面单 1-子2"},
        *[
            {
                **duplicate_row,
                "source_label": f"面单 1-子{index}",
                "sales_attr2_text": str(index),
            }
            for index in range(3, 12)
        ],
    ]
    warning_only = analyze_waybill_coverage(
        expected_parent_counts={10: 1},
        rows=warning_rows,
        normal_export_count=0,
        exception_export_count=11,
    )

    assert warning_only["ok"] is True
    assert warning_only["failures"] == []
    assert warning_only["warnings"] == [
        {"code": "duplicate_child_rows", "count": 1},
        {"code": "high_child_count", "count": 1},
    ]

    lost = analyze_waybill_coverage(
        expected_parent_counts={10: 1, 20: 1},
        rows=[duplicate_row],
        normal_export_count=0,
        exception_export_count=0,
    )

    assert lost["ok"] is False
    assert lost["failures"] == [
        {"code": "parent_count_mismatch", "count": 1},
        {"code": "raw_record_uncovered", "count": 1},
        {"code": "export_coverage_mismatch", "count": 1},
    ]
