from copy import deepcopy
import json
from pathlib import Path

from services.shared.waybill_fingerprint import fingerprint_for_payload

from scripts.migrate_validation_rule_pack_v2 import (
    CANDIDATE_FILENAME,
    REPORT_FILENAME,
    migrate_rule_pack,
    write_migration,
)


def row(product: str, quantity: int = 1) -> dict:
    return {
        "product": product,
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": quantity,
        "remark": "",
    }


def profile(payload: dict, source_component: str = "collector", *, product_path: str = "name") -> dict:
    return {
        "fingerprint": fingerprint_for_payload(payload, source_component, "legacy_structure_v1"),
        "strategy": "structured_items_v1",
        "items_path": "items[]",
        "fields": {"product": product_path, "quantity": "quantity"},
    }


def package_profile(payload: dict, *, product_path: str) -> dict:
    return {
        "fingerprint": fingerprint_for_payload(payload, "cainiao-cnprint", "legacy_structure_v1"),
        "strategy": "structured_items_v1",
        "items_path": "contents[].data.packageItemDetail[]",
        "fields": {"product": product_path, "quantity": "itemNum"},
    }


def pack(profiles: list[dict], *, learning_records: list[dict] | None = None) -> dict:
    result = {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "source-pack", "name": "Source pack", "version": "1.0.0"},
        "parser_policy": {
            "order_row_parser": "declarative_v1",
            "fingerprint_strategy": "legacy_structure_v1",
            "format_profiles": profiles,
        },
        "product_matching_policy": {},
        "export_policy": {},
    }
    if learning_records is not None:
        result["ai_learning_records"] = learning_records
    return result


def validation(payload: dict, expected_rows: list[dict], source_component: str = "collector") -> dict:
    return {
        "source_component": source_component,
        "payload": payload,
        "expected_rows": expected_rows,
    }


def test_migration_is_deterministic_idempotent_and_preserves_duplicate_rows() -> None:
    first_payload = {"items": [{"name": "PRIVATE-SHOE-A", "quantity": 2}], "customer": "PRIVATE-ALICE"}
    duplicate_payload = {
        "items": [
            {"name": "PRIVATE-SHOE-B", "quantity": 1},
            {"name": "PRIVATE-SHOE-B", "quantity": 1},
        ],
        "customer": "PRIVATE-BOB",
    }
    source = pack(
        [profile(first_payload)],
        learning_records=[
            {
                "session_id": "session-private",
                "fingerprint": fingerprint_for_payload(first_payload, "collector", "legacy_structure_v1"),
                "source_component": "collector",
                "sample_payload": first_payload,
                "confirmed_rows": [row("PRIVATE-SHOE-A", 2)],
            }
        ],
    )
    records = [
        validation(first_payload, [row("PRIVATE-SHOE-A", 2)]),
        validation(duplicate_payload, [row("PRIVATE-SHOE-B"), row("PRIVATE-SHOE-B")]),
    ]
    original_source = deepcopy(source)
    original_records = deepcopy(records)

    first_candidate, first_report = migrate_rule_pack(
        source,
        records,
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )
    second_candidate, second_report = migrate_rule_pack(
        source,
        records,
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert first_candidate == second_candidate
    assert first_report == second_report
    assert source == original_source
    assert records == original_records
    assert first_report["candidate_ready"] is True
    assert first_report["counts"] == {
        "validation_samples": 2,
        "learning_samples_checked": 1,
        "groups_total": 1,
        "groups_verified": 1,
        "groups_unresolved": 0,
        "verified_profiles": 1,
    }
    assert first_candidate is not None
    assert first_candidate["pack"]["code"] == "source-pack-v2-candidate"
    assert first_candidate["pack"]["version"] == "2.0.0"
    assert first_candidate["parser_policy"]["fingerprint_strategy"] == "business_shape_v2"
    assert len(first_candidate["parser_policy"]["format_profiles"]) == 1
    assert "ai_learning_records" not in first_candidate
    assert "PRIVATE-" not in json.dumps(first_report, ensure_ascii=False)


def test_profile_body_conflict_leaves_group_unresolved_without_blocking_verified_group() -> None:
    verified_payload = {"items": [{"name": "safe", "quantity": 1}]}
    package_a = {
        "contents": [{"data": {"packageItemDetail": [{"itemName": "shoe-a", "simpleName": "short-a", "itemNum": 1}]}}]
    }
    package_b = {
        "contents": [
            {
                "data": {
                    "packageItemDetail": [{"itemName": "shoe-b", "simpleName": "short-b", "itemNum": 1}],
                    "unrelated": "changes legacy only",
                }
            }
        ]
    }
    source = pack(
        [
            profile(verified_payload),
            package_profile(package_a, product_path="itemName"),
            package_profile(package_b, product_path="simpleName"),
        ]
    )

    candidate, report = migrate_rule_pack(
        source,
        [
            validation(verified_payload, [row("safe")]),
            validation(package_a, [row("shoe-a")], "cainiao-cnprint"),
            validation(package_b, [row("short-b")], "cainiao-cnprint"),
        ],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert report["candidate_ready"] is True
    assert report["counts"]["groups_verified"] == 1
    assert report["counts"]["groups_unresolved"] == 1
    assert any(item["reason"] == "profile_body_conflict" for item in report["diagnostics"])
    assert candidate is not None
    assert len(candidate["parser_policy"]["format_profiles"]) == 1


def test_complete_learning_record_must_exactly_replay() -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack(
        [profile(payload)],
        learning_records=[
            {
                "session_id": "session-1",
                "fingerprint": fingerprint_for_payload(payload, "collector", "legacy_structure_v1"),
                "source_component": "collector",
                "sample_payload": payload,
                "confirmed_rows": [row("different")],
            }
        ],
    )

    candidate, report = migrate_rule_pack(
        source,
        [validation(payload, [row("shoe")])],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert candidate is None
    assert report["candidate_ready"] is False
    assert report["diagnostics"][0]["reason"] == "learning_replay_mismatch"


def test_learning_record_profile_body_participates_in_group_conflict_check() -> None:
    validation_payload = {
        "contents": [
            {"data": {"packageItemDetail": [{"itemName": "shoe", "simpleName": "shoe", "itemNum": 1}]}}
        ]
    }
    learning_payload = {
        "contents": [
            {
                "data": {
                    "packageItemDetail": [{"itemName": "shoe", "simpleName": "shoe", "itemNum": 1}],
                    "unrelated": "different legacy shape",
                }
            }
        ]
    }
    learning_fingerprint = fingerprint_for_payload(
        learning_payload, "cainiao-cnprint", "legacy_structure_v1"
    )
    source = pack(
        [
            package_profile(validation_payload, product_path="itemName"),
            package_profile(learning_payload, product_path="simpleName"),
        ],
        learning_records=[
            {
                "session_id": "session-1",
                "fingerprint": learning_fingerprint,
                "source_component": "cainiao-cnprint",
                "sample_payload": learning_payload,
                "confirmed_rows": [row("shoe")],
            }
        ],
    )

    candidate, report = migrate_rule_pack(
        source,
        [validation(validation_payload, [row("shoe")], "cainiao-cnprint")],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert candidate is None
    assert report["candidate_ready"] is False
    assert report["diagnostics"][0]["reason"] == "profile_body_conflict"
    assert report["diagnostics"][0]["source_profile_body_count"] == 2


def test_incomplete_learning_record_is_not_treated_as_replay_evidence() -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack(
        [profile(payload)],
        learning_records=[
            {
                "session_id": "old-incomplete-session",
                "source_component": "collector",
                "sample_payload": payload,
                "confirmed_rows": [row("shoe")],
                "document_sequence": "not-an-integer",
            }
        ],
    )

    candidate, report = migrate_rule_pack(
        source,
        [validation(payload, [row("shoe")])],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert candidate is not None
    assert report["candidate_ready"] is True
    assert report["counts"]["learning_samples_checked"] == 0


def test_candidate_code_guard_and_missing_evidence_do_not_forge_candidate() -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack([profile(payload)])

    same_code_candidate, same_code_report = migrate_rule_pack(
        source,
        [validation(payload, [row("shoe")])],
        candidate_code="source-pack",
        candidate_version="2.0.0",
    )
    missing_candidate, missing_report = migrate_rule_pack(
        source,
        [{"source_component": "collector", "payload": payload}],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert same_code_candidate is None
    assert same_code_report["reasons"] == ["candidate_code_matches_source"]
    assert missing_candidate is None
    assert missing_report["candidate_ready"] is False
    assert "validation_evidence_missing" in missing_report["reasons"]


def test_preview_validator_failure_blocks_candidate() -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack([profile(payload)])

    candidate, report = migrate_rule_pack(
        source,
        [validation(payload, [row("shoe")])],
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
        previewer=lambda _pack, _record: {"status": "rule_pack_invalid", "rows": []},
    )

    assert candidate is None
    assert report["candidate_ready"] is False
    assert "preview_validation_failed" in report["reasons"]


def test_writer_uses_only_fixed_files_beneath_output_dir_and_removes_stale_candidate(tmp_path: Path) -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack([profile(payload)])
    records = [validation(payload, [row("shoe")])]
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    source_path = input_dir / "source.json"
    records_path = input_dir / "records.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    records_path.write_text(json.dumps(records), encoding="utf-8")
    source_before = source_path.read_bytes()
    records_before = records_path.read_bytes()

    first = write_migration(
        source_path,
        records_path,
        output_dir,
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )
    first_candidate_bytes = (output_dir / CANDIDATE_FILENAME).read_bytes()
    first_report_bytes = (output_dir / REPORT_FILENAME).read_bytes()
    second = write_migration(
        source_path,
        records_path,
        output_dir,
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert first == second
    assert (output_dir / CANDIDATE_FILENAME).read_bytes() == first_candidate_bytes
    assert (output_dir / REPORT_FILENAME).read_bytes() == first_report_bytes
    assert {path.name for path in output_dir.iterdir()} == {CANDIDATE_FILENAME, REPORT_FILENAME}
    assert source_path.read_bytes() == source_before
    assert records_path.read_bytes() == records_before

    blocked = write_migration(
        source_path,
        records_path,
        output_dir,
        candidate_code="source-pack",
        candidate_version="2.0.0",
    )
    assert blocked["candidate_ready"] is False
    assert not (output_dir / CANDIDATE_FILENAME).exists()
    assert {path.name for path in output_dir.iterdir()} == {REPORT_FILENAME}


def test_writer_replaces_output_hardlink_without_mutating_input(tmp_path: Path) -> None:
    payload = {"items": [{"name": "shoe", "quantity": 1}]}
    source = pack([profile(payload)])
    records = [validation(payload, [row("shoe")])]
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "source.json"
    records_path = input_dir / "records.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    records_path.write_text(json.dumps(records), encoding="utf-8")
    source_before = source_path.read_bytes()
    candidate_path = output_dir / CANDIDATE_FILENAME
    candidate_path.hardlink_to(source_path)

    write_migration(
        source_path,
        records_path,
        output_dir,
        candidate_code="source-pack-v2-candidate",
        candidate_version="2.0.0",
    )

    assert source_path.read_bytes() == source_before
    assert candidate_path.samefile(source_path) is False
