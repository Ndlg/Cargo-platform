from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Callable

from services.shared.waybill_fingerprint import fingerprint_for_payload


CANDIDATE_FILENAME = "candidate-rule-pack-v2.json"
REPORT_FILENAME = "migration-report.json"
ROW_FIELDS = ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
Previewer = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return f"sha256:{sha256(_json_bytes(value)).hexdigest()}"


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _profile_body(profile: dict[str, Any]) -> dict[str, Any]:
    body = deepcopy(profile)
    body.pop("fingerprint", None)
    return body


def _business_rows(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list) or not value:
        return None
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        row = {field: item.get(field, "") for field in ROW_FIELDS}
        if (
            not isinstance(row["product"], str)
            or not row["product"].strip()
            or any(not isinstance(row[field], str) for field in ("sales_attr1", "sales_attr2", "remark"))
            or not isinstance(row["quantity"], int)
            or isinstance(row["quantity"], bool)
            or not 1 <= row["quantity"] <= 100_000
        ):
            return None
        rows.append(row)
    return rows


def _row_multiset(rows: list[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
    return Counter(tuple(row.get(field, "") for field in ROW_FIELDS) for row in rows)


def _preview_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("rows")
    if not isinstance(rows, list):
        rows = [
            row
            for parent in result.get("parents") or []
            if isinstance(parent, dict)
            for row in parent.get("rows") or []
            if isinstance(row, dict)
        ]
    return [{field: row.get(field, "") for field in ROW_FIELDS} for row in rows if isinstance(row, dict)]


def _select_document(payload: dict[str, Any], document_sequence: int) -> dict[str, Any] | None:
    task = payload.get("task")
    documents = task.get("documents") if isinstance(task, dict) else None
    if not isinstance(documents, list) or not documents:
        return payload
    if document_sequence > len(documents) or not isinstance(documents[document_sequence - 1], dict):
        return None
    return {
        **payload,
        "task": {
            **task,
            "documents": [documents[document_sequence - 1]],
        },
    }


def local_parser_preview(rule_pack: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    parser_root = Path(__file__).resolve().parents[1] / "services" / "waybill-parser"
    parser_root_text = str(parser_root)
    if parser_root_text not in sys.path:
        sys.path.insert(0, parser_root_text)
    from service_app.main import BatchParseRequest, parse_preview

    request = BatchParseRequest(
        task_id=1,
        raw_records=[
            {
                "raw_record_id": 1,
                "task_id": 1,
                "document_sequence": int(record.get("document_sequence") or 1),
                "source_component": record["source_component"],
                "source_index": record["sample_hash"],
                "payload": record["payload"],
            }
        ],
        rule_pack=rule_pack,
        allow_ai=False,
    )
    return parse_preview(request)


def _candidate_pack(
    source_pack: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    candidate_code: str,
    candidate_version: str,
) -> dict[str, Any]:
    candidate = deepcopy(source_pack)
    candidate.pop("ai_learning_records", None)
    candidate["pack"] = {
        **candidate["pack"],
        "code": candidate_code,
        "version": candidate_version,
    }
    candidate["parser_policy"] = {
        **candidate["parser_policy"],
        "fingerprint_strategy": "business_shape_v2",
        "format_profiles": sorted(profiles, key=lambda item: item["fingerprint"]),
    }
    return candidate


def _empty_report(source_pack: Any, validation_records: Any) -> dict[str, Any]:
    validation_count = len(validation_records) if isinstance(validation_records, list) else 0
    return {
        "schema_version": "rule_pack_v2_migration_report_v1",
        "source_pack_hash": _hash(source_pack),
        "validation_records_hash": _hash(validation_records),
        "status": "not_ready",
        "candidate_ready": False,
        "candidate_hash": None,
        "counts": {
            "validation_samples": validation_count,
            "learning_samples_checked": 0,
            "groups_total": 0,
            "groups_verified": 0,
            "groups_unresolved": 0,
            "verified_profiles": 0,
        },
        "reasons": [],
        "diagnostics": [],
    }


def migrate_rule_pack(
    source_pack: dict[str, Any],
    validation_records: list[dict[str, Any]],
    *,
    candidate_code: str,
    candidate_version: str,
    previewer: Previewer | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    source = deepcopy(source_pack)
    records = deepcopy(validation_records)
    report = _empty_report(source, records)
    source_meta = source.get("pack") if isinstance(source, dict) else None
    parser_policy = source.get("parser_policy") if isinstance(source, dict) else None
    source_code = str(source_meta.get("code") or "").strip() if isinstance(source_meta, dict) else ""
    candidate_code = str(candidate_code or "").strip()
    candidate_version = str(candidate_version or "").strip()

    if not candidate_code:
        report["reasons"] = ["candidate_code_missing"]
        return None, report
    if candidate_code == source_code:
        report["reasons"] = ["candidate_code_matches_source"]
        return None, report
    if not candidate_version:
        report["reasons"] = ["candidate_version_missing"]
        return None, report
    if (
        not source_code
        or not isinstance(parser_policy, dict)
        or parser_policy.get("order_row_parser") != "declarative_v1"
        or parser_policy.get("fingerprint_strategy", "legacy_structure_v1") != "legacy_structure_v1"
        or not isinstance(parser_policy.get("format_profiles"), list)
        or not isinstance(records, list)
        or not records
    ):
        report["reasons"] = ["source_or_validation_contract_invalid"]
        return None, report

    profiles_by_legacy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in parser_policy["format_profiles"]:
        if isinstance(item, dict) and isinstance(item.get("fingerprint"), str):
            profiles_by_legacy[item["fingerprint"]].append(item)

    samples: list[dict[str, Any]] = []
    for record in records:
        source_component = record.get("source_component") if isinstance(record, dict) else None
        payload = record.get("payload") if isinstance(record, dict) else None
        expected_rows = _business_rows(record.get("expected_rows")) if isinstance(record, dict) else None
        sequence = record.get("document_sequence", 1) if isinstance(record, dict) else None
        if (
            not isinstance(source_component, str)
            or not source_component.strip()
            or not isinstance(payload, dict)
            or expected_rows is None
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
        ):
            report["reasons"] = ["validation_evidence_missing"]
            return None, report
        document_payload = _select_document(payload, sequence)
        if document_payload is None:
            report["reasons"] = ["validation_evidence_missing"]
            return None, report
        sample_hash = _hash(
            {
                "source_component": source_component.strip(),
                "document_sequence": sequence,
                "payload": document_payload,
                "expected_rows": expected_rows,
            }
        )
        samples.append(
            {
                "source_component": source_component.strip(),
                "document_sequence": sequence,
                "payload": document_payload,
                "expected_rows": expected_rows,
                "sample_hash": sample_hash,
                "legacy_fingerprint": fingerprint_for_payload(
                    document_payload, source_component, "legacy_structure_v1"
                ),
                "v2_fingerprint": fingerprint_for_payload(
                    document_payload, source_component, "business_shape_v2"
                ),
            }
        )

    preview = previewer or local_parser_preview
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[sample["v2_fingerprint"]].append(sample)
    report["counts"]["groups_total"] = len(groups)

    learning_by_v2: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in source.get("ai_learning_records", []):
        if not isinstance(item, dict):
            continue
        learning_payload = item.get("sample_payload")
        source_component = item.get("source_component")
        expected_rows = _business_rows(item.get("confirmed_rows"))
        declared_fingerprint = item.get("fingerprint")
        document_sequence = item.get("document_sequence", 1)
        if (
            not isinstance(learning_payload, dict)
            or not isinstance(source_component, str)
            or not source_component.strip()
            or expected_rows is None
            or not isinstance(declared_fingerprint, str)
            or not declared_fingerprint
            or not isinstance(document_sequence, int)
            or isinstance(document_sequence, bool)
            or document_sequence < 1
        ):
            continue
        learning_sample = {
            "source_component": source_component.strip(),
            "document_sequence": document_sequence,
            "payload": learning_payload,
            "expected_rows": expected_rows,
            "sample_hash": _hash(
                {
                    "source_component": source_component.strip(),
                    "payload": learning_payload,
                    "expected_rows": expected_rows,
                }
            ),
            "legacy_fingerprint": fingerprint_for_payload(
                learning_payload, source_component, "legacy_structure_v1"
            ),
            "declared_fingerprint": declared_fingerprint,
            "v2_fingerprint": fingerprint_for_payload(
                learning_payload, source_component, "business_shape_v2"
            ),
        }
        learning_by_v2[learning_sample["v2_fingerprint"]].append(learning_sample)

    verified_profiles: list[dict[str, Any]] = []
    verified_samples: list[dict[str, Any]] = []
    fatal_preview_failure = False
    for v2_fingerprint in sorted(groups):
        group_samples = groups[v2_fingerprint]
        learning_samples = learning_by_v2.get(v2_fingerprint, [])
        all_group_samples = [*group_samples, *learning_samples]
        diagnostic = {
            "fingerprint": v2_fingerprint,
            "status": "unresolved",
            "reason": "",
            "validation_sample_count": len(group_samples),
            "learning_sample_count": len(learning_samples),
            "source_profile_body_count": 0,
            "sample_hashes": sorted(sample["sample_hash"] for sample in all_group_samples),
        }
        report["counts"]["learning_samples_checked"] += len(learning_samples)
        reason = ""
        bodies: dict[bytes, dict[str, Any]] = {}
        for sample in all_group_samples:
            if (
                "declared_fingerprint" in sample
                and sample["declared_fingerprint"] != sample["legacy_fingerprint"]
            ):
                reason = "learning_fingerprint_mismatch"
                break
            source_profiles = profiles_by_legacy.get(sample["legacy_fingerprint"], [])
            if not source_profiles:
                reason = "legacy_profile_missing"
                break
            for source_profile in source_profiles:
                body = _profile_body(source_profile)
                bodies[_json_bytes(body)] = body
            try:
                baseline = preview(source, sample)
            except Exception:
                fatal_preview_failure = True
                reason = "preview_validation_failed"
                break
            if baseline.get("status") == "rule_pack_invalid":
                fatal_preview_failure = True
                reason = "preview_validation_failed"
                break
            if baseline.get("status") != "parsed":
                reason = "legacy_baseline_unparseable"
                break
            if _row_multiset(_preview_rows(baseline)) != _row_multiset(sample["expected_rows"]):
                reason = (
                    "learning_replay_mismatch"
                    if "declared_fingerprint" in sample
                    else "legacy_baseline_mismatch"
                )
                break
        diagnostic["source_profile_body_count"] = len(bodies)
        if not reason and len(bodies) != 1:
            reason = "profile_body_conflict"

        candidate_profile = None
        if not reason:
            candidate_profile = {**next(iter(bodies.values())), "fingerprint": v2_fingerprint}
            group_candidate = _candidate_pack(
                source,
                [candidate_profile],
                candidate_code=candidate_code,
                candidate_version=candidate_version,
            )
            for sample in all_group_samples:
                try:
                    replay = preview(group_candidate, sample)
                except Exception:
                    fatal_preview_failure = True
                    reason = "preview_validation_failed"
                    break
                if replay.get("status") == "rule_pack_invalid":
                    fatal_preview_failure = True
                    reason = "preview_validation_failed"
                    break
                if (
                    replay.get("status") != "parsed"
                    or _row_multiset(_preview_rows(replay)) != _row_multiset(sample["expected_rows"])
                ):
                    reason = (
                        "learning_replay_mismatch"
                        if "declared_fingerprint" in sample
                        else "validation_replay_mismatch"
                    )
                    break

        if reason:
            diagnostic["reason"] = reason
            report["counts"]["groups_unresolved"] += 1
        else:
            diagnostic["status"] = "verified"
            diagnostic["reason"] = "exact_replay"
            report["counts"]["groups_verified"] += 1
            verified_profiles.append(candidate_profile)
            verified_samples.extend(group_samples)
        report["diagnostics"].append(diagnostic)

    report["counts"]["verified_profiles"] = len(verified_profiles)
    reasons: list[str] = []
    if fatal_preview_failure:
        reasons.append("preview_validation_failed")
    if not verified_profiles:
        reasons.append("no_verified_groups")
    if report["counts"]["groups_unresolved"]:
        reasons.append("unresolved_groups_present")
    if fatal_preview_failure or not verified_profiles:
        report["reasons"] = reasons
        return None, report

    candidate = _candidate_pack(
        source,
        verified_profiles,
        candidate_code=candidate_code,
        candidate_version=candidate_version,
    )
    for sample in verified_samples:
        try:
            final_preview = preview(candidate, sample)
        except Exception:
            report["reasons"] = ["preview_validation_failed"]
            return None, report
        if (
            final_preview.get("status") != "parsed"
            or _row_multiset(_preview_rows(final_preview)) != _row_multiset(sample["expected_rows"])
        ):
            report["reasons"] = ["preview_validation_failed"]
            return None, report

    report["status"] = "candidate_ready"
    report["candidate_ready"] = True
    report["candidate_hash"] = _hash(candidate)
    report["reasons"] = reasons
    return candidate, report


def write_migration(
    source_path: Path | str,
    validation_path: Path | str,
    output_dir: Path | str,
    *,
    candidate_code: str,
    candidate_version: str,
    previewer: Previewer | None = None,
) -> dict[str, Any]:
    source_path = Path(source_path).resolve()
    validation_path = Path(validation_path).resolve()
    output_dir = Path(output_dir).resolve()
    candidate_path = output_dir / CANDIDATE_FILENAME
    report_path = output_dir / REPORT_FILENAME
    if source_path in {candidate_path, report_path} or validation_path in {candidate_path, report_path}:
        raise ValueError("input files must be outside the fixed migration output files")

    source_pack = json.loads(source_path.read_text(encoding="utf-8"))
    validation_records = json.loads(validation_path.read_text(encoding="utf-8"))
    candidate, report = migrate_rule_pack(
        source_pack,
        validation_records,
        candidate_code=candidate_code,
        candidate_version=candidate_version,
        previewer=previewer,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if candidate is None:
        candidate_path.unlink(missing_ok=True)
    else:
        candidate_path.unlink(missing_ok=True)
        candidate_path.write_bytes(_pretty_json(candidate))
    report_path.unlink(missing_ok=True)
    report_path.write_bytes(_pretty_json(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline business_shape_v2 rule-pack candidate.")
    parser.add_argument("--source-pack", type=Path, required=True)
    parser.add_argument("--validation-records", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-code", required=True)
    parser.add_argument("--candidate-version", required=True)
    args = parser.parse_args(argv)
    report = write_migration(
        args.source_pack,
        args.validation_records,
        args.output_dir,
        candidate_code=args.candidate_code,
        candidate_version=args.candidate_version,
    )
    print(json.dumps({"candidate_ready": report["candidate_ready"], "reasons": report["reasons"]}, ensure_ascii=False))
    return 0 if report["candidate_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
