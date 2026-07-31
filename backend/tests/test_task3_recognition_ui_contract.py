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
PARSER_ISSUES = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "parserIssues.ts"
)
PROFILE_EDITOR = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "components"
    / "recognition"
    / "RecognitionProfileEditor.vue"
)
RULE_PACKS_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "RecognitionRulePacksView.vue"
)

PARSER_EXCEPTION_STATUSES = (
    "model_running",
    "approving",
    "ai_rule_pending",
    "candidate_invalid",
    "ai_rule_invalid",
    "ai_result_invalid",
    "ai_unavailable",
    "ai_parse_failed",
    "fingerprint_adapter_required",
    "fingerprint_field_selection_required",
    "format_profile_missing",
    "format_profile_incomplete",
    "rule_pack_missing",
    "rule_pack_invalid",
)


def test_parser_exception_coverage_is_independent_from_status_allowlists() -> None:
    exceptions_source = EXCEPTIONS_VIEW.read_text(encoding="utf-8")
    batches_source = WAYBILL_BATCHES_VIEW.read_text(encoding="utf-8")
    parser_issues_source = PARSER_ISSUES.read_text(encoding="utf-8")

    assert "if (!orderDrafts.value) return 0" in exceptions_source
    assert "if (!aiStatus.value || !orderDrafts.value) return 0" not in exceptions_source
    assert "parserIssueFor(" in exceptions_source
    assert "parserIssueFor(" in batches_source
    for status in PARSER_EXCEPTION_STATUSES:
        assert status in parser_issues_source
    for route in (
        "/admin/ai-recognition",
        "/admin/fingerprint-settings",
        "/admin/recognition-rule-packs",
    ):
        assert route in parser_issues_source


def test_rule_profile_business_summary_precedes_collapsed_technical_details() -> None:
    editor_source = PROFILE_EDITOR.read_text(encoding="utf-8")
    packs_source = RULE_PACKS_VIEW.read_text(encoding="utf-8")

    collapse_index = editor_source.index('<el-collapse v-model="openSections"')
    assert editor_source.index("确认时的五字段结果") < collapse_index
    assert editor_source.index("确认时的脱敏字段样本") > collapse_index
    assert editor_source.index("技术来源") > collapse_index
    assert "learningRecordFor(profile)?.source_component" not in packs_source
    assert "profileValidationLabel(profile)" in packs_source
