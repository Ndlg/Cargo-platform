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
    "export_header_definitions",
    "print_template_configs",
}

RESEEDED_TABLES = {"tenant_fingerprint_configs"}

CLEARED_TABLES = {
    "exception_records",
    "export_records",
    "field_definitions",
    "field_role_configs",
    "key_field_sets",
    "match_rules",
    "operation_logs",
    "recognition_rule_packs",
    "recognition_rule_pack_revisions",
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
ORACLE_PARSER_URL = "http://127.0.0.1:8010"
TRUE_ZERO_FINGERPRINT_FIELDS = {
    "CN-ITEM-INFO": ["item_info", "seller_memo", "item_total_count"],
    "CN-PRINT-XML": ["print_text"],
    "CN-CUSTOM-CONTENT": ["custom_content"],
    "CN-PACKAGE-ITEMS": [
        "item_name",
        "sku_full_name",
        "spec_name",
        "sku_size",
        "item_quantity",
    ],
    "CLOUD-PRODUCT-INFO": [
        "product_info",
        "product_short_info",
        "spec_info",
        "remark",
        "product_count",
    ],
}
TRUE_ZERO_TASK_IDS = (64, 65, 66)
TRUE_ZERO_RELEASE_COUNTS = {
    "capture_tasks": 3,
    "raw_capture_records": 102,
    "products": 23,
    "product_skus": 2004,
    "image_assets": 2360,
    "stalls": 6,
    "product_matching_rules": 37,
    "recognition_rule_packs": 0,
    "recognition_rule_pack_revisions": 0,
    "tenant_fingerprint_configs": 5,
}
TRUE_ZERO_RAW_PAYLOAD_SHA256 = (
    "5feaa6ef0bbf8563d232ba8b4f661be30604c6cd10e0c7d7db612725a71d7033"
)
TRUE_ZERO_ASSET_COUNT = 2271
TRUE_ZERO_ASSET_MANIFEST_SHA256 = (
    "87e94f3becf98797c274b89e787e1c9c05703b9a99f115aebe8e8d838025f12b"
)


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
    known = PRESERVED_TABLES | RESEEDED_TABLES | CLEARED_TABLES | IGNORED_TABLES
    return sorted(
        table
        for table in table_names(db) - known
        if table_count(db, table) > 0
    )


def _prune_capture_tasks(db: sqlite3.Connection, task_ids: list[int]) -> list[int]:
    requested = sorted(task_ids)
    if requested != list(TRUE_ZERO_TASK_IDS):
        raise ValueError(
            "true-zero task_ids must be explicitly set to 64, 65, and 66"
        )
    present = table_names(db)
    if "capture_tasks" not in present:
        raise ValueError("capture_tasks table is missing")
    task_columns = table_columns(db, "capture_tasks")
    if not {"id", "status"} <= task_columns:
        raise ValueError("capture_tasks must contain id and status columns")

    placeholders = ", ".join("?" for _ in requested)
    active_clause = ' AND "is_deleted" = 0' if "is_deleted" in task_columns else ""
    completed = {
        int(row[0])
        for row in db.execute(
            f"""
            SELECT "id" FROM "capture_tasks"
            WHERE "id" IN ({placeholders}) AND "status" = 'completed'{active_clause}
            """,
            requested,
        )
    }
    missing = sorted(set(requested) - completed)
    if missing:
        raise ValueError(f"selected capture tasks are missing or not completed: {missing}")

    for table in ("raw_capture_records", "capture_batches"):
        if table not in present:
            continue
        columns = table_columns(db, table)
        task_column = next(
            (column for column in ("task_id", "capture_task_id") if column in columns),
            None,
        )
        if task_column is None:
            if table_count(db, table):
                raise ValueError(f"{table} has no task reference column")
            continue
        db.execute(
            f'DELETE FROM "{table}" '
            f'WHERE "{task_column}" IS NULL OR "{task_column}" NOT IN ({placeholders})',
            requested,
        )
    db.execute(
        f'DELETE FROM "capture_tasks" WHERE "id" NOT IN ({placeholders})',
        requested,
    )
    return requested


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


def _require_columns(
    db: sqlite3.Connection,
    table: str,
    required: set[str],
) -> None:
    if table not in table_names(db):
        raise ValueError(f"true-zero database requires {table}")
    missing = required - table_columns(db, table)
    if missing:
        raise ValueError(
            f"{table} is missing required columns: {', '.join(sorted(missing))}"
        )


def _verify_true_zero_release(
    db: sqlite3.Connection,
    *,
    expected_counts: dict[str, int],
    expected_raw_payload_sha256: str,
) -> dict[str, object]:
    present = table_names(db)
    missing_tables = set(expected_counts) - present
    if missing_tables:
        raise ValueError(
            "true-zero release is missing tables: "
            + ", ".join(sorted(missing_tables))
        )
    cleared_counts = {
        table: table_count(db, table)
        for table in sorted(CLEARED_TABLES & present)
    }
    nonempty_cleared_tables = {
        table: count
        for table, count in cleared_counts.items()
        if count != 0
    }
    if nonempty_cleared_tables:
        raise ValueError(
            "true-zero release cleared tables are not empty: "
            + json.dumps(
                nonempty_cleared_tables,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    actual_counts = {
        table: table_count(db, table)
        for table in expected_counts
    }
    count_mismatches = {
        table: {"expected": expected_counts[table], "actual": actual_counts[table]}
        for table in expected_counts
        if actual_counts[table] != expected_counts[table]
    }
    if count_mismatches:
        raise ValueError(
            "true-zero release count mismatch: "
            + json.dumps(count_mismatches, ensure_ascii=False, sort_keys=True)
        )

    task_ids = [
        int(row[0])
        for row in db.execute('SELECT "id" FROM "capture_tasks" ORDER BY "id"')
    ]
    if task_ids != list(TRUE_ZERO_TASK_IDS):
        raise ValueError(f"true-zero release task ids mismatch: {task_ids}")
    derived_count = int(
        db.execute(
            """
            SELECT COUNT(*)
            FROM raw_capture_records
            WHERE status <> 'pending'
               OR parsed_payload IS NOT NULL
               OR standard_detail_id IS NOT NULL
               OR waybill_mode IS NOT NULL
               OR archived_at IS NOT NULL
               OR archived_by IS NOT NULL
            """
        ).fetchone()[0]
    )
    if derived_count:
        raise ValueError(
            f"true-zero release still has {derived_count} derived raw records"
        )
    payload_sha = raw_payload_sha256(db)
    if payload_sha != expected_raw_payload_sha256:
        raise ValueError(
            "true-zero raw payload hash mismatch: "
            f"expected {expected_raw_payload_sha256}, got {payload_sha}"
        )
    fingerprint_configs = {
        str(code): json.loads(selected_fields)
        for code, selected_fields in db.execute(
            """
            SELECT fingerprint_code, selected_fields
            FROM tenant_fingerprint_configs
            WHERE is_enabled = 1 AND is_deleted = 0
            ORDER BY fingerprint_code
            """
        )
    }
    if fingerprint_configs != dict(sorted(TRUE_ZERO_FINGERPRINT_FIELDS.items())):
        raise ValueError("true-zero fingerprint configurations do not match the release contract")
    return {
        "counts": actual_counts,
        "task_ids": task_ids,
        "raw_derived_count": derived_count,
        "raw_payload_sha256": payload_sha,
        "fingerprint_count": len(fingerprint_configs),
        "cleared_counts": cleared_counts,
    }


def asset_manifest(asset_root: Path) -> dict[str, object]:
    root = asset_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = sha256()
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        content_digest = bytes.fromhex(sha256_file(path))
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_digest)
    return {
        "asset_count": len(files),
        "asset_manifest_sha256": digest.hexdigest(),
    }


def verify_true_zero_volume(
    database: Path,
    asset_root: Path,
) -> dict[str, object]:
    with closing(readonly_connection(database)) as db:
        _require_columns(
            db,
            "raw_capture_records",
            {
                "status",
                "parsed_payload",
                "standard_detail_id",
                "waybill_mode",
                "archived_at",
                "archived_by",
            },
        )
        _require_columns(
            db,
            "tenant_fingerprint_configs",
            {
                "fingerprint_code",
                "is_enabled",
                "is_deleted",
                "selected_fields",
            },
        )
        database_verification = _verify_true_zero_release(
            db,
            expected_counts=TRUE_ZERO_RELEASE_COUNTS,
            expected_raw_payload_sha256=TRUE_ZERO_RAW_PAYLOAD_SHA256,
        )
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_key_errors:
        raise ValueError(
            f"true-zero SQLite validation failed: integrity={integrity}, "
            f"foreign_keys={foreign_key_errors}"
        )
    assets = asset_manifest(asset_root)
    if (
        assets["asset_count"] != TRUE_ZERO_ASSET_COUNT
        or assets["asset_manifest_sha256"] != TRUE_ZERO_ASSET_MANIFEST_SHA256
    ):
        raise ValueError(
            "true-zero asset manifest mismatch: "
            + json.dumps(assets, ensure_ascii=False, sort_keys=True)
        )
    return {
        "contract_version": "true_zero_volume_verification_v1",
        "database": database_verification,
        "integrity_check": integrity,
        "foreign_key_check": "ok",
        **assets,
    }


def _seed_true_zero_fingerprint_configs(
    db: sqlite3.Connection,
    *,
    workspace_id: int,
) -> int:
    present = table_names(db)
    if "workspaces" not in present or "tenant_fingerprint_configs" not in present:
        raise ValueError(
            "true-zero database requires workspaces and tenant_fingerprint_configs tables"
        )
    workspace_columns = table_columns(db, "workspaces")
    if not {"id", "tenant_id"} <= workspace_columns:
        raise ValueError("workspaces must contain id and tenant_id columns")
    row = db.execute(
        'SELECT "tenant_id" FROM "workspaces" WHERE "id" = ?',
        (workspace_id,),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"workspace {workspace_id} has no tenant")
    tenant_id = int(row[0])

    config_columns = table_columns(db, "tenant_fingerprint_configs")
    required = {"tenant_id", "fingerprint_code", "is_enabled", "selected_fields"}
    if not required <= config_columns:
        raise ValueError(
            "tenant_fingerprint_configs is missing required columns: "
            + ", ".join(sorted(required - config_columns))
        )
    db.execute('DELETE FROM "tenant_fingerprint_configs"')
    insert_columns = [
        "tenant_id",
        "fingerprint_code",
        "is_enabled",
        "selected_fields",
    ]
    if "is_deleted" in config_columns:
        insert_columns.append("is_deleted")
    placeholders = ", ".join("?" for _ in insert_columns)
    columns_sql = ", ".join(f'"{column}"' for column in insert_columns)
    for code, selected_fields in TRUE_ZERO_FINGERPRINT_FIELDS.items():
        values: list[object] = [
            tenant_id,
            code,
            1,
            json.dumps(selected_fields, ensure_ascii=False),
        ]
        if "is_deleted" in config_columns:
            values.append(0)
        db.execute(
            f'INSERT INTO "tenant_fingerprint_configs" ({columns_sql}) '
            f"VALUES ({placeholders})",
            values,
        )
    return tenant_id


def build_cold_start_database(
    source_db: Path,
    destination_db: Path,
    *,
    task_ids: list[int] | None = None,
    workspace_id: int = 1,
    _expected_counts: dict[str, int] = TRUE_ZERO_RELEASE_COUNTS,
    _expected_raw_payload_sha256: str = TRUE_ZERO_RAW_PAYLOAD_SHA256,
) -> dict[str, object]:
    if task_ids is None:
        raise ValueError("true-zero task_ids must be explicitly set to 64, 65, and 66")
    requested_task_ids = sorted(task_ids)
    if requested_task_ids != list(TRUE_ZERO_TASK_IDS):
        raise ValueError(
            "true-zero task_ids must be explicitly set to 64, 65, and 66"
        )
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
            target.execute("PRAGMA foreign_keys = ON")
            source_connection.backup(target)
            unknown = _unknown_nonempty_tables(target)
            if unknown:
                raise ValueError(f"unknown nonempty tables: {', '.join(unknown)}")

            present = table_names(target)
            _require_columns(
                target,
                "collectors",
                {
                    "token_hash",
                    "is_enabled",
                    "online_status",
                    "last_heartbeat_at",
                    "status_payload",
                },
            )
            _require_columns(
                target,
                "raw_capture_records",
                {
                    "status",
                    "parsed_payload",
                    "standard_detail_id",
                    "waybill_mode",
                    "archived_at",
                    "archived_by",
                },
            )
            target.execute("BEGIN IMMEDIATE")
            target.execute("PRAGMA defer_foreign_keys = ON")
            try:
                selected_task_ids = (
                    _prune_capture_tasks(target, task_ids)
                    if task_ids is not None
                    else None
                )
                for table in sorted(CLEARED_TABLES & present):
                    target.execute(f'DELETE FROM "{table}"')

                tenant_id = _seed_true_zero_fingerprint_configs(
                    target,
                    workspace_id=workspace_id,
                )
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
                        "status": "pending",
                        "parsed_payload": None,
                        "standard_detail_id": None,
                        "waybill_mode": None,
                        "archived_at": None,
                        "archived_by": None,
                    },
                )
                target.commit()
            except BaseException:
                target.rollback()
                raise
            integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"cold-start database integrity check failed: {integrity}")
            foreign_key_errors = target.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise RuntimeError(
                    f"cold-start database foreign key check failed: {foreign_key_errors}"
                )
            preserved_counts = {
                table: table_count(target, table)
                for table in sorted(PRESERVED_TABLES & present)
            }
            cleared_counts = {
                table: table_count(target, table)
                for table in sorted(CLEARED_TABLES & present)
            }
            payload_sha = raw_payload_sha256(target)
            fingerprint_configs = {
                str(code): json.loads(selected_fields)
                for code, selected_fields in target.execute(
                    """
                    SELECT fingerprint_code, selected_fields
                    FROM tenant_fingerprint_configs
                    WHERE tenant_id = ? AND is_enabled = 1
                    ORDER BY fingerprint_code
                    """,
                    (tenant_id,),
                )
            }
            release_verification = _verify_true_zero_release(
                target,
                expected_counts=_expected_counts,
                expected_raw_payload_sha256=_expected_raw_payload_sha256,
            )

        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    if sha256_file(source) != source_sha:
        destination.unlink(missing_ok=True)
        raise RuntimeError("source database changed while cold-start copy was built")

    return {
        "contract_version": "ai_validation_dataset_v2",
        "source_path": str(source),
        "destination_path": str(destination),
        "source_sha256": source_sha,
        "destination_sha256": sha256_file(destination),
        "raw_payload_sha256": payload_sha,
        "integrity_check": integrity,
        "foreign_key_check": "ok",
        "selected_task_ids": selected_task_ids,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "fingerprint_configs": fingerprint_configs,
        "release_verification": release_verification,
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


def _completed_task_ids(db: sqlite3.Connection, task_ids: list[int] | None) -> list[int]:
    if not task_ids:
        return [
            int(row[0])
            for row in db.execute(
                """
                SELECT id FROM capture_tasks
                WHERE status = 'completed' AND is_deleted = 0
                ORDER BY id
                """
            )
        ]
    requested = sorted(set(task_ids))
    placeholders = ", ".join("?" for _ in requested)
    completed = [
        int(row[0])
        for row in db.execute(
            f"""
            SELECT id FROM capture_tasks
            WHERE status = 'completed' AND is_deleted = 0 AND id IN ({placeholders})
            ORDER BY id
            """,
            requested,
        )
    ]
    missing = sorted(set(requested) - set(completed))
    if missing:
        raise ValueError(f"selected capture tasks are not completed: {missing}")
    return completed


def _expected_parent_order(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "raw_record_id": parent.get("raw_record_id"),
            "parent_label": parent.get("parent_label"),
        }
        for parent in response.get("parents") or []
        if isinstance(parent, dict)
    ]


def _gold_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parent_index, parent in enumerate(response.get("parents") or [], start=1):
        if not isinstance(parent, dict):
            continue
        for child_index, row in enumerate(parent.get("rows") or [], start=1):
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "raw_record_id": row.get("raw_record_id"),
                    "parent_index": parent_index,
                    "child_index": child_index,
                    "fields": {
                        field: row.get(field)
                        for field in ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
                    },
                }
            )
    return rows


def _without_rule_payloads(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_rule_payloads(item)
            for key, item in value.items()
            if key not in {"recognition_rule_pack", "rule_pack", "ai_sessions", "ai_session_rules"}
        }
    if isinstance(value, list):
        return [_without_rule_payloads(item) for item in value]
    return value


def _answer_set_entry(
    task_id: int, raw_records: list[dict[str, Any]], response: dict[str, Any]
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "raw_record_ids": [row["raw_record_id"] for row in raw_records],
        "raw_records": raw_records,
        "expected_parent_order": _expected_parent_order(response),
        "gold_rows": _gold_rows(response),
        "response": _without_rule_payloads(response),
    }


def export_answer_set(
    source_db: Path,
    parser_url: str,
    output: Path,
    *,
    task_ids: list[int] | None = None,
) -> dict[str, object]:
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
            for task_id in _completed_task_ids(db, task_ids):
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
                        _answer_set_entry(task_id, raw_records, response),
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


def export_gold_rows(
    source_db: Path,
    parser_url: str,
    output: Path,
    *,
    task_ids: list[int] | None = None,
    exclude_rule_tables: bool = False,
) -> dict[str, object]:
    if not exclude_rule_tables:
        raise ValueError("gold row export requires --exclude-rule-tables")
    source = source_db.resolve()
    gold_output = output.resolve()
    manifest_path = gold_output.with_name("oracle-manifest.json")
    if not source.is_file():
        raise FileNotFoundError(source)
    if gold_output.exists() or manifest_path.exists():
        raise FileExistsError(gold_output if gold_output.exists() else manifest_path)
    gold_output.parent.mkdir(parents=True, exist_ok=True)

    parser_base = parser_url.rstrip("/")
    if parser_base != ORACLE_PARSER_URL:
        raise ValueError(f"gold row export only reads the 5173 oracle parser at {ORACLE_PARSER_URL}")
    parser_health = request_json(f"{parser_base}/health")
    source_sha = sha256_file(source)
    temporary = gold_output.with_name(f".{gold_output.name}.{uuid4().hex}.tmp")
    task_count = 0
    raw_record_count = 0
    try:
        with closing(readonly_connection(source)) as db, temporary.open(
            "w", encoding="utf-8", newline="\n"
        ) as stream:
            rule_pack = _active_rule_pack(db)
            for task_id in _completed_task_ids(db, task_ids):
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
                entry = _answer_set_entry(task_id, raw_records, response)
                entry.pop("raw_record_ids")
                entry.pop("response")
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                task_count += 1
                raw_record_count += len(raw_records)
        temporary.replace(gold_output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        gold_output.unlink(missing_ok=True)
        raise

    if sha256_file(source) != source_sha:
        gold_output.unlink(missing_ok=True)
        raise RuntimeError("source database changed while gold rows were exported")

    manifest: dict[str, object] = {
        "contract_version": "ai_recognition_gold_rows_v1",
        "source_path": str(source),
        "source_sha256": source_sha,
        "gold_output_path": str(gold_output),
        "gold_output_sha256": sha256_file(gold_output),
        "parser_url": parser_base,
        "parser_health": parser_health,
        "task_count": task_count,
        "raw_record_count": raw_record_count,
        "exclude_rule_tables": exclude_rule_tables,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = ArgumentParser(description="Create an isolated cold-start AI validation dataset.")
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--cold-db", type=Path)
    parser.add_argument("--answer-set", type=Path)
    parser.add_argument("--task-id", action="append", type=int)
    parser.add_argument("--gold-output", type=Path)
    parser.add_argument("--exclude-rule-tables", action="store_true")
    parser.add_argument("--parser-url")
    parser.add_argument(
        "--true-zero",
        action="store_true",
        help="build only a zero-rule cold database; cannot be mixed with oracle inputs",
    )
    parser.add_argument("--verify-database", type=Path)
    parser.add_argument("--asset-root", type=Path)
    args = parser.parse_args()
    if args.verify_database is not None:
        if args.asset_root is None:
            parser.error("--verify-database requires --asset-root")
        if any(
            value is not None
            for value in (
                args.source_db,
                args.cold_db,
                args.answer_set,
                args.gold_output,
                args.parser_url,
            )
        ) or args.task_id or args.exclude_rule_tables or args.true_zero:
            parser.error("--verify-database cannot be combined with build or oracle options")
        print(
            json.dumps(
                verify_true_zero_volume(args.verify_database, args.asset_root),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.source_db is None:
        parser.error("--source-db is required")
    if args.asset_root is not None:
        parser.error("--asset-root is only valid with --verify-database")
    if args.cold_db is None and args.answer_set is None and args.gold_output is None:
        parser.error("provide --cold-db, --answer-set, --gold-output, or a combination")
    if (args.answer_set is not None or args.gold_output is not None) and not args.parser_url:
        parser.error("--answer-set and --gold-output require --parser-url")
    if args.cold_db is not None and not args.true_zero:
        parser.error("--cold-db requires --true-zero")
    if args.true_zero and (
        args.answer_set is not None
        or args.gold_output is not None
        or args.parser_url is not None
        or args.exclude_rule_tables
    ):
        parser.error(
            "--true-zero accepts only --source-db, --cold-db, and optional --task-id"
        )
    if args.true_zero and args.cold_db is None:
        parser.error("--true-zero requires --cold-db")

    results: dict[str, object] = {}
    if args.answer_set is not None:
        results["answer_set"] = export_answer_set(
            args.source_db, args.parser_url, args.answer_set, task_ids=args.task_id
        )
    if args.gold_output is not None:
        results["gold_rows"] = export_gold_rows(
            args.source_db,
            args.parser_url,
            args.gold_output,
            task_ids=args.task_id,
            exclude_rule_tables=args.exclude_rule_tables,
        )
    if args.cold_db is not None:
        results["cold_start"] = build_cold_start_database(
            args.source_db,
            args.cold_db,
            task_ids=args.task_id,
        )
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
