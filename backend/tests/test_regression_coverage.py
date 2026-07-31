from app.services.regression_coverage import analyze_waybill_coverage


def parent(raw_record_id: int, parent_sequence: int) -> dict[str, int]:
    return {
        "raw_record_id": raw_record_id,
        "parent_sequence": parent_sequence,
    }


def row(
    raw_record_id: int,
    parent_sequence: int,
    *,
    child_index: int = 1,
    status: str = "matched",
    reason: str = "",
    product: str = "同款商品",
) -> dict[str, object]:
    return {
        "raw_record_id": raw_record_id,
        "source_label": f"第1批-第{parent_sequence}单-子{child_index}",
        "product_text": product,
        "sales_attr1_text": "黑色",
        "sales_attr2_text": "42",
        "quantity_text": "1",
        "remark_text": "",
        "status": status,
        "reason": reason,
    }


def test_parent_partition_keeps_repeated_prints_and_multi_product_children() -> None:
    result = analyze_waybill_coverage(
        expected_parent_documents=[
            parent(10, 1),
            parent(11, 2),
            parent(20, 3),
        ],
        rows=[
            row(10, 1),
            row(11, 2),
            row(20, 3, child_index=1, product="商品甲"),
            row(20, 3, child_index=2, product="商品乙"),
        ],
        normal_export_count=4,
        exception_export_count=0,
    )

    assert result["ok"] is True
    assert result["expected_parent_count"] == 3
    assert result["normal_parent_count"] == 3
    assert result["exception_parent_count"] == 0
    assert result["result_row_count"] == 4
    assert result["warnings"] == []


def test_mixed_parent_with_actionable_diagnostic_is_not_double_counted() -> None:
    result = analyze_waybill_coverage(
        expected_parent_documents=[parent(30, 1)],
        rows=[
            row(30, 1, child_index=1),
            row(
                30,
                1,
                child_index=2,
                status="future_parser_diagnostic",
                reason="新解析状态，请维护识别规则。",
                product="商品乙",
            ),
        ],
        normal_export_count=1,
        exception_export_count=1,
    )

    assert result["ok"] is True
    assert result["normal_parent_count"] == 0
    assert result["exception_parent_count"] == 1
    assert result["covered_parent_count"] == 1
    assert result["parent_partition_disjoint"] is True
    assert result["parent_partition_complete"] is True
    assert result["warnings"] == [{"code": "mixed_parent_outcomes", "count": 1}]


def test_unknown_status_requires_an_actionable_reason() -> None:
    result = analyze_waybill_coverage(
        expected_parent_documents=[parent(40, 1)],
        rows=[row(40, 1, status="future_status")],
        normal_export_count=0,
        exception_export_count=1,
    )

    assert result["ok"] is False
    assert result["normal_parent_count"] == 0
    assert result["exception_parent_count"] == 0
    assert result["failures"] == [
        {"code": "exception_not_actionable", "count": 1},
        {"code": "parent_partition_incomplete", "count": 1},
    ]


def test_equal_parent_counts_do_not_hide_the_wrong_collected_document() -> None:
    result = analyze_waybill_coverage(
        expected_parent_documents=[parent(50, 1)],
        rows=[row(999, 1)],
        normal_export_count=1,
        exception_export_count=0,
    )

    assert result["expected_parent_count"] == result["recognized_parent_count"] == 1
    assert result["parent_partition_complete"] is False
    assert result["ok"] is False
    assert result["failures"] == [
        {"code": "raw_record_uncovered", "count": 1},
        {"code": "parent_partition_incomplete", "count": 2},
    ]


def test_collected_parent_identity_collision_is_not_silently_deduplicated() -> None:
    result = analyze_waybill_coverage(
        expected_parent_documents=[parent(60, 1), parent(60, 1)],
        rows=[row(60, 1)],
        normal_export_count=1,
        exception_export_count=0,
    )

    assert result["ok"] is False
    assert result["failures"] == [
        {"code": "expected_parent_identity_collision", "count": 1},
        {"code": "parent_count_mismatch", "count": 1},
    ]


def test_waybill_coverage_distinguishes_blocking_loss_from_child_warnings() -> None:
    duplicate_row = row(10, 1)
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
        expected_parent_documents=[parent(10, 1)],
        rows=warning_rows,
        normal_export_count=11,
        exception_export_count=0,
    )

    assert warning_only["ok"] is True
    assert warning_only["failures"] == []
    assert warning_only["warnings"] == [
        {"code": "duplicate_child_rows", "count": 1},
        {"code": "high_child_count", "count": 1},
    ]

    lost = analyze_waybill_coverage(
        expected_parent_documents=[parent(10, 1), parent(20, 2)],
        rows=[duplicate_row],
        normal_export_count=1,
        exception_export_count=0,
    )

    assert lost["ok"] is False
    assert lost["failures"] == [
        {"code": "parent_count_mismatch", "count": 1},
        {"code": "raw_record_uncovered", "count": 1},
        {"code": "parent_partition_incomplete", "count": 1},
    ]
