from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import Thread
from typing import Iterator

import pytest

import scripts.ai_validation_dataset as validation_dataset
from scripts.ai_validation_dataset import (
    build_cold_start_database,
    export_answer_set,
    export_gold_rows,
    sha256_file,
)


def create_source_database(path: Path, *, with_unknown_table: bool = False) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE collectors (
                id INTEGER PRIMARY KEY,
                collector_id TEXT,
                collector_name TEXT,
                token_hash TEXT,
                is_enabled INTEGER,
                online_status TEXT,
                last_heartbeat_at TEXT,
                status_payload TEXT
            );
            CREATE TABLE capture_tasks (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                status TEXT,
                is_deleted INTEGER DEFAULT 0
            );
            CREATE TABLE capture_batches (id INTEGER PRIMARY KEY, task_id INTEGER);
            CREATE TABLE raw_capture_records (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                task_id INTEGER,
                source_component TEXT,
                source_index TEXT,
                raw_payload TEXT,
                source_columns TEXT,
                parsed_payload TEXT,
                standard_detail_id INTEGER,
                waybill_mode TEXT,
                archived_at TEXT,
                is_deleted INTEGER DEFAULT 0
            );
            CREATE TABLE stalls (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE product_skus (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT);
            CREATE TABLE image_assets (id INTEGER PRIMARY KEY, name TEXT, file_path TEXT);
            CREATE TABLE product_matching_rules (id INTEGER PRIMARY KEY, product_id INTEGER);
            CREATE TABLE recognition_rule_packs (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                payload TEXT,
                status TEXT,
                is_enabled INTEGER,
                is_deleted INTEGER DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE standard_details (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                field_values TEXT,
                archived_at TEXT,
                is_deleted INTEGER DEFAULT 0
            );
            CREATE TABLE export_records (id INTEGER PRIMARY KEY, payload TEXT);
            """
        )
        db.execute(
            "INSERT INTO collectors VALUES (1, 'right', '右边', 'secret-hash', 1, 'online', 'now', '{\"pid\": 1}')"
        )
        db.executemany(
            "INSERT INTO capture_tasks(id, workspace_id, status, is_deleted) VALUES (?, 1, 'completed', 0)",
            [(11,), (12,)],
        )
        db.executemany(
            """
            INSERT INTO raw_capture_records(
                id, workspace_id, task_id, source_component, source_index,
                raw_payload, source_columns, parsed_payload, standard_detail_id,
                waybill_mode, archived_at, is_deleted
            ) VALUES (?, 1, ?, 'cainiao-cnprint', ?, ?, '{"商品":"范74"}',
                      '{"product":"old"}', 99, 'old-mode', NULL, 0)
            """,
            [
                (101, 11, "1", '{"task":{"documents":[{"contents":[{"data":{"商品":"范74"}}]}]}}'),
                (102, 12, "2", '{"task":{"documents":[{"contents":[{"data":{"商品":"秒45"}}]}]}}'),
            ],
        )
        db.execute("INSERT INTO stalls VALUES (1, '至尚')")
        db.execute("INSERT INTO products VALUES (1, '范74')")
        db.execute("INSERT INTO product_skus VALUES (1, 1, '45')")
        db.execute("INSERT INTO image_assets VALUES (1, '范74-45', 'storage/范74-45.png')")
        db.execute("INSERT INTO product_matching_rules VALUES (1, 1)")
        db.execute(
            """
            INSERT INTO recognition_rule_packs
            VALUES (1, 1, ?, 'active', 1, 0, '2026-07-29')
            """,
            (json.dumps({"contract_version": "recognition_rule_pack_v1", "pack": {"code": "baseline"}}),),
        )
        db.execute(
            "INSERT INTO standard_details VALUES (1, 1, ?, NULL, 0)",
            (json.dumps({"capture_task_id": 11, "product": "范74", "quantity": "1"}),),
        )
        db.execute("INSERT INTO export_records VALUES (1, '{\"old\":true}')")
        if with_unknown_table:
            db.execute("CREATE TABLE future_derived_results (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO future_derived_results VALUES (1)")
        db.commit()


def raw_payload_digest(path: Path) -> str:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT id, raw_payload, source_columns FROM raw_capture_records ORDER BY id"
        ).fetchall()
    return sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_build_cold_start_database_preserves_inputs_and_scrubs_results(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source)
    source_hash = sha256_file(source)
    payload_hash = raw_payload_digest(source)

    manifest = build_cold_start_database(source, destination)

    assert sha256_file(source) == source_hash
    assert raw_payload_digest(destination) == payload_hash
    assert manifest["integrity_check"] == "ok"
    assert manifest["source_sha256"] == source_hash
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM product_skus").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM image_assets").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM product_matching_rules").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM raw_capture_records").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM recognition_rule_packs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM standard_details").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM export_records").fetchone()[0] == 0
        collector = db.execute(
            """
            SELECT token_hash, is_enabled, online_status, last_heartbeat_at, status_payload
            FROM collectors
            """
        ).fetchone()
        assert collector == (None, 0, "offline", None, None)
        derived = db.execute(
            "SELECT parsed_payload, standard_detail_id, waybill_mode FROM raw_capture_records ORDER BY id"
        ).fetchall()
        assert derived == [(None, None, None), (None, None, None)]


def test_build_cold_start_database_rejects_unknown_nonempty_table(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source, with_unknown_table=True)

    with pytest.raises(ValueError, match="future_derived_results"):
        build_cold_start_database(source, destination)

    assert not destination.exists()


def test_build_cold_start_database_never_overwrites_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source)
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        build_cold_start_database(source, destination)

    assert destination.read_bytes() == b"keep"


@contextmanager
def fake_parser() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"status": "ok", "version": "answer-set-test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            requests.append(request)
            task_id = request["task_id"]
            raw_records = request["raw_records"]
            body = json.dumps(
                {
                    "contract_version": "order_row_drafts_v1",
                    "task_id": task_id,
                    "status": "parsed",
                    "summary": {
                        "parent_waybill_count": len(raw_records),
                        "child_waybill_count": len(raw_records),
                        "draft_count": len(raw_records),
                        "needs_review_count": 0,
                        "special_count": 0,
                    },
                    "parents": [
                        {
                            "raw_record_id": row["raw_record_id"],
                            "parent_label": f"parent-{row['raw_record_id']}",
                            "rows": [
                                {
                                    "raw_record_id": row["raw_record_id"],
                                    "product": f"task-{task_id}",
                                    "sales_attr1": "红",
                                    "sales_attr2": "40",
                                    "quantity": 1,
                                    "remark": "",
                                }
                            ],
                        }
                        for row in raw_records
                    ],
                    "rows": [
                        {
                            "raw_record_id": row["raw_record_id"],
                            "product": f"task-{task_id}",
                            "sales_attr1": "红",
                            "sales_attr2": "40",
                            "quantity": 1,
                            "remark": "",
                        }
                        for row in raw_records
                    ],
                    "recognition_rule_pack": {"payload": "must-not-export"},
                    "ai_sessions": [{"rules": "must-not-export"}],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_export_answer_set_uses_active_pack_and_records_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "answer-set.jsonl"
    create_source_database(source)

    with fake_parser() as (parser_url, requests):
        manifest = export_answer_set(source, parser_url, output)

    assert len(requests) == 2
    assert all(request["rule_pack"]["pack"]["code"] == "baseline" for request in requests)
    assert [request["task_id"] for request in requests] == [11, 12]
    assert output.exists()
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["task_id"] for row in rows] == [11, 12]
    assert manifest["task_count"] == 2
    assert manifest["raw_record_count"] == 2
    assert manifest["source_sha256"] == sha256_file(source)
    assert manifest["answer_set_sha256"] == sha256_file(output)
    assert manifest["parser_health"]["version"] == "answer-set-test"
    manifest_path = output.with_suffix(".manifest.json")
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert "recognition_rule_pack" not in rows[0]["response"]
    assert "ai_sessions" not in rows[0]["response"]


def test_export_gold_rows_keeps_raw_payloads_and_excludes_rule_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "gold-rows.jsonl"
    create_source_database(source)

    with fake_parser() as (parser_url, requests):
        monkeypatch.setattr(validation_dataset, "ORACLE_PARSER_URL", parser_url)
        manifest = export_gold_rows(
            source,
            parser_url,
            output,
            task_ids=[12],
            exclude_rule_tables=True,
        )

    assert [request["task_id"] for request in requests] == [12]
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "task_id": 12,
            "raw_records": [
                {
                    "raw_record_id": 102,
                    "task_id": 12,
                    "source_component": "cainiao-cnprint",
                    "source_index": "2",
                    "payload": {"task": {"documents": [{"contents": [{"data": {"商品": "秒45"}}]}]}},
                }
            ],
            "expected_parent_order": [
                {"raw_record_id": 102, "parent_label": "parent-102"}
            ],
            "gold_rows": [
                {
                    "raw_record_id": 102,
                    "parent_index": 1,
                    "child_index": 1,
                    "fields": {
                        "product": "task-12",
                        "sales_attr1": "红",
                        "sales_attr2": "40",
                        "quantity": 1,
                        "remark": "",
                    },
                }
            ],
        }
    ]
    assert "recognition_rule_packs" not in output.read_text(encoding="utf-8")
    assert manifest["gold_output_sha256"] == sha256_file(output)
    assert json.loads((tmp_path / "oracle-manifest.json").read_text(encoding="utf-8")) == manifest


def test_export_gold_rows_rejects_non_oracle_before_sending_rule_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "gold-rows.jsonl"
    create_source_database(source)
    monkeypatch.setattr(validation_dataset, "ORACLE_PARSER_URL", "http://127.0.0.1:8010")

    with fake_parser() as (non_oracle_url, requests), pytest.raises(ValueError, match="5173 oracle"):
        export_gold_rows(
            source,
            non_oracle_url,
            output,
            task_ids=[12],
            exclude_rule_tables=True,
        )

    assert requests == []
    assert not output.exists()
