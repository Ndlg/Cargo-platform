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
AI_RECOGNITION_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "views"
    / "workbench"
    / "AiRecognitionView.vue"
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


def test_parser_issue_actions_follow_the_zero_rule_and_unsupported_format_flows() -> None:
    source = PARSER_ISSUES.read_text(encoding="utf-8")
    adapter_block = source.split("fingerprint_adapter_required:", 1)[1].split(
        "fingerprint_field_selection_required:",
        1,
    )[0]
    missing_pack_block = source.split("rule_pack_missing:", 1)[1].split(
        "rule_pack_invalid:",
        1,
    )[0]

    assert "系统尚未支持该格式，请联系维护" in adapter_block
    assert "action: 'refresh'" in adapter_block
    assert "action: 'fingerprint-settings'" not in adapter_block
    assert "action: 'ai-recognition'" in missing_pack_block
    assert "action: 'recognition-rule-packs'" not in missing_pack_block


def test_ai_recognition_prefers_a_valid_task_query_without_exposing_ids_in_labels() -> None:
    source = AI_RECOGNITION_VIEW.read_text(encoding="utf-8")

    assert "const route = useRoute()" in source
    assert "queryPositiveInt(route.query.task_id)" in source
    assert "taskIds.has(routeTaskId)" in source
    assert "selectedTaskId.value = routeTaskId" in source
    assert "最近一轮" in source
    assert "上一轮" in source
    assert "task.id}`" not in source


def test_rule_profile_business_summary_precedes_collapsed_technical_details() -> None:
    editor_source = PROFILE_EDITOR.read_text(encoding="utf-8")
    packs_source = RULE_PACKS_VIEW.read_text(encoding="utf-8")

    collapse_index = editor_source.index('<el-collapse v-model="openSections"')
    assert editor_source.index("确认时的五字段结果") < collapse_index
    assert editor_source.index("确认时的脱敏字段样本") > collapse_index
    assert editor_source.index("技术来源") > collapse_index
    assert "learningRecordFor(profile)?.source_component" not in packs_source
    assert "profileValidationLabel(profile)" in packs_source
