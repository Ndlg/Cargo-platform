from __future__ import annotations

from argparse import ArgumentParser
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4


PRESERVED_TABLES = {
    "tenants",
    "workspaces",
    "users",
    "roles",
    "user_workspaces",
    "collectors",
    "capture_tasks",
    "capture_batches",
    "raw_capture_records",
    "stalls",
    "products",
    "product_skus",
    "image_assets",
    "product_matching_rules",
}

CLEARED_TABLES = {
    "exception_records",
    "export_header_definitions",
    "export_records",
    "field_definitions",
    "field_role_configs",
    "key_field_sets",
    "match_rules",
    "operation_logs",
    "print_template_configs",
    "recognition_rule_packs",
    "report_batches",
    "report_lines",
    "standard_detail_batches",
    "standard_details",
    "waybill_field_mapping_results",
    "waybill_field_mapping_rules",
    "waybill_modes",
    "waybill_template_fields",
    "waybill_templates",
}

IGNORED_TABLES = {"alembic_version", "sqlite_sequence"}


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def table_names(db: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}


def table_count(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def raw_payload_sha256(db: sqlite3.Connection) -> str:
    if "raw_capture_records" not in table_names(db):
        return sha256(b"[]").hexdigest()
    columns = table_columns(db, "raw_capture_records")
    selected = ["id", "raw_payload"]
    if "source_columns" in columns:
        selected.append("source_columns")
    rows = db.execute(
        f'SELECT {", ".join(selected)} FROM "raw_capture_records" ORDER BY id'
    ).fetchall()
    return sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _unknown_nonempty_tables(db: sqlite3.Connection) -> list[str]:
    known = PRESERVED_TABLES | CLEARED_TABLES | IGNORED_TABLES
    return sorted(
        table
        for table in table_names(db) - known
        if table_count(db, table) > 0
    )


def _scrub_columns(
    db: sqlite3.Connection,
    table: str,
    assignments: dict[str, object],
) -> None:
    if table not in table_names(db):
        return
    columns = table_columns(db, table)
    selected = [(name, value) for name, value in assignments.items() if name in columns]
    if not selected:
        return
    clause = ", ".join(f'"{name}" = ?' for name, _value in selected)
    db.execute(f'UPDATE "{table}" SET {clause}', [value for _name, value in selected])


def build_cold_start_database(source_db: Path, destination_db: Path) -> dict[str, object]:
    source = source_db.resolve()
    destination = destination_db.resolve()
    if source == destination:
        raise ValueError("source and destination database must be different files")
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    source_sha = sha256_file(source)
    try:
        with closing(readonly_connection(source)) as source_connection, closing(
            sqlite3.connect(temporary)
        ) as target:
            source_connection.backup(target)
            unknown = _unknown_nonempty_tables(target)
            if unknown:
                raise ValueError(f"unknown nonempty tables: {', '.join(unknown)}")

            present = table_names(target)
            for table in sorted(CLEARED_TABLES & present):
                target.execute(f'DELETE FROM "{table}"')

            _scrub_columns(
                target,
                "collectors",
                {
                    "token_hash": None,
                    "is_enabled": 0,
                    "online_status": "offline",
                    "last_heartbeat_at": None,
                    "status_payload": None,
                },
            )
            _scrub_columns(
                target,
                "raw_capture_records",
                {
                    "parsed_payload": None,
                    "standard_detail_id": None,
                    "waybill_mode": None,
                },
            )
            target.commit()
            integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"cold-start database integrity check failed: {integrity}")
            preserved_counts = {
                table: table_count(target, table)
                for table in sorted(PRESERVED_TABLES & present)
            }
            cleared_counts = {
                table: table_count(target, table)
                for table in sorted(CLEARED_TABLES & present)
            }
            payload_sha = raw_payload_sha256(target)

        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    if sha256_file(source) != source_sha:
        destination.unlink(missing_ok=True)
        raise RuntimeError("source database changed while cold-start copy was built")

    return {
        "contract_version": "ai_validation_dataset_v1",
        "source_path": str(source),
        "destination_path": str(destination),
        "source_sha256": source_sha,
        "destination_sha256": sha256_file(destination),
        "raw_payload_sha256": payload_sha,
        "integrity_check": integrity,
        "preserved_counts": preserved_counts,
        "cleared_counts": cleared_counts,
    }


def request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=60) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"expected JSON object from {url}")
    return decoded


def _active_rule_pack(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute(
        """
        SELECT payload
        FROM recognition_rule_packs
        WHERE is_deleted = 0 AND is_enabled = 1 AND status = 'active'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise ValueError("source database has no active recognition rule pack")
    payload = json.loads(row[0])
    if not isinstance(payload, dict):
        raise ValueError("active recognition rule pack payload is not an object")
    return payload


def _raw_records_for_task(db: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, task_id, source_component, source_index, raw_payload
        FROM raw_capture_records
        WHERE task_id = ? AND is_deleted = 0
        ORDER BY id
        """,
        (task_id,),
    ).fetchall()
    records: list[dict[str, Any]] = []
    for raw_record_id, row_task_id, source_component, source_index, raw_payload in rows:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            payload = {"raw_text": str(raw_payload or "")}
        if not isinstance(payload, dict):
            payload = {"raw_value": payload}
        records.append(
            {
                "raw_record_id": int(raw_record_id),
                "task_id": int(row_task_id),
                "source_component": source_component,
                "source_index": source_index,
                "payload": payload,
            }
        )
    return records


def export_answer_set(source_db: Path, parser_url: str, output: Path) -> dict[str, object]:
    source = source_db.resolve()
    answer_set = output.resolve()
    manifest_path = answer_set.with_suffix(".manifest.json")
    if not source.is_file():
        raise FileNotFoundError(source)
    if answer_set.exists() or manifest_path.exists():
        raise FileExistsError(answer_set if answer_set.exists() else manifest_path)
    answer_set.parent.mkdir(parents=True, exist_ok=True)

    parser_base = parser_url.rstrip("/")
    parser_health = request_json(f"{parser_base}/health")
    source_sha = sha256_file(source)
    temporary = answer_set.with_name(f".{answer_set.name}.{uuid4().hex}.tmp")
    task_count = 0
    raw_record_count = 0
    try:
        with closing(readonly_connection(source)) as db, temporary.open(
            "w", encoding="utf-8", newline="\n"
        ) as stream:
            rule_pack = _active_rule_pack(db)
            task_ids = [
                int(row[0])
                for row in db.execute(
                    """
                    SELECT id FROM capture_tasks
                    WHERE status = 'completed' AND is_deleted = 0
                    ORDER BY id
                    """
                )
            ]
            for task_id in task_ids:
                raw_records = _raw_records_for_task(db, task_id)
                if not raw_records:
                    continue
                response = request_json(
                    f"{parser_base}/api/v1/parse/batch",
                    {
                        "task_id": task_id,
                        "standard_details": [],
                        "raw_records": raw_records,
                        "waybill_samples": [],
                        "rule_pack": rule_pack,
                    },
                )
                if response.get("contract_version") != "order_row_drafts_v1":
                    raise ValueError(f"parser contract mismatch for task {task_id}")
                stream.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "raw_record_ids": [row["raw_record_id"] for row in raw_records],
                            "response": response,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                task_count += 1
                raw_record_count += len(raw_records)
        temporary.replace(answer_set)
    except BaseException:
        temporary.unlink(missing_ok=True)
        answer_set.unlink(missing_ok=True)
        raise

    if sha256_file(source) != source_sha:
        answer_set.unlink(missing_ok=True)
        raise RuntimeError("source database changed while answer set was exported")

    manifest: dict[str, object] = {
        "contract_version": "ai_recognition_answer_set_v1",
        "source_path": str(source),
        "source_sha256": source_sha,
        "answer_set_path": str(answer_set),
        "answer_set_sha256": sha256_file(answer_set),
        "parser_url": parser_base,
        "parser_health": parser_health,
        "task_count": task_count,
        "raw_record_count": raw_record_count,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = ArgumentParser(description="Create an isolated cold-start AI validation dataset.")
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--cold-db", type=Path)
    parser.add_argument("--answer-set", type=Path)
    parser.add_argument("--parser-url")
    args = parser.parse_args()
    if args.cold_db is None and args.answer_set is None:
        parser.error("provide --cold-db, --answer-set, or both")
    if args.answer_set is not None and not args.parser_url:
        parser.error("--answer-set requires --parser-url")

    results: dict[str, object] = {}
    if args.answer_set is not None:
        results["answer_set"] = export_answer_set(args.source_db, args.parser_url, args.answer_set)
    if args.cold_db is not None:
        results["cold_start"] = build_cold_start_database(args.source_db, args.cold_db)
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
