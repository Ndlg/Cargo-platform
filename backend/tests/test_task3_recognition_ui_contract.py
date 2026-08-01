from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS_VIEW = (
    PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ExceptionsView.vue"
)
WAYBILL_BATCHES_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "WaybillBatchesView.vue"
)
FORMAT_LEARNING_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "FormatLearningView.vue"
)
RULE_PACKS_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "RecognitionRulePacksView.vue"
)


def test_parser_exception_coverage_is_independent_from_status_allowlists() -> None:
    exceptions_source = EXCEPTIONS_VIEW.read_text(encoding="utf-8")
    batches_source = WAYBILL_BATCHES_VIEW.read_text(encoding="utf-8")

    assert "if (!orderDrafts.value) return 0" in exceptions_source
    assert "if (!aiStatus.value || !orderDrafts.value) return 0" not in exceptions_source
    assert "parserIssueFor(" in exceptions_source
    assert "parserIssueFor(" in batches_source


def test_format_learning_shows_field_evidence_before_rule_sample_rows() -> None:
    learning_source = FORMAT_LEARNING_VIEW.read_text(encoding="utf-8")
    packs_source = RULE_PACKS_VIEW.read_text(encoding="utf-8")

    assert learning_source.index("用于生成规则的脱敏字段") < learning_source.index(
        "用于生成规则的样本结果"
    )
    assert "expected_evidence_sha256: prepared.value.evidence_sha256" in learning_source
    assert "修改不会直接覆盖订单行" in learning_source
    assert "学习记录只读" in packs_source
    assert "RecognitionProfileEditor" not in packs_source
