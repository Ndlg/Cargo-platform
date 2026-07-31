from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Thread
from typing import Iterator

import pytest

import scripts.ai_validation_dataset as validation_dataset
from scripts.ai_validation_dataset import (
    TRUE_ZERO_FINGERPRINT_FIELDS,
    TRUE_ZERO_TASK_IDS,
    asset_manifest,
    build_cold_start_database,
    export_answer_set,
    export_gold_rows,
    sha256_file,
)


def create_source_database(
    path: Path,
    *,
    with_unknown_table: bool = False,
    with_true_zero_records: bool = False,
) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE tenants (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                name TEXT
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT
            );
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
                archived_by INTEGER,
                status TEXT,
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
            CREATE TABLE export_header_definitions (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE print_template_configs (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE export_records (id INTEGER PRIMARY KEY, payload TEXT);
            CREATE TABLE recognition_rule_pack_revisions (
                id INTEGER PRIMARY KEY,
                payload TEXT
            );
            CREATE TABLE tenant_fingerprint_configs (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                fingerprint_code TEXT NOT NULL,
                is_enabled INTEGER NOT NULL,
                selected_fields TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                UNIQUE (tenant_id, fingerprint_code)
            );
            """
        )
        db.executemany(
            "INSERT INTO tenants(id, name) VALUES (?, ?)",
            [(1, "验证租户"), (2, "应清除租户")],
        )
        db.execute("INSERT INTO workspaces VALUES (1, 1, '验证工作区')")
        db.execute("INSERT INTO users VALUES (1, 'Administrator')")
        db.execute(
            "INSERT INTO collectors VALUES (1, 'right', '右边', 'secret-hash', 1, 'online', 'now', '{\"pid\": 1}')"
        )
        db.executemany(
            "INSERT INTO capture_tasks(id, workspace_id, status, is_deleted) VALUES (?, 1, 'completed', 0)",
            [(11,), (12,), (64,), (65,), (66,)],
        )
        db.executemany(
            """
            INSERT INTO raw_capture_records(
                id, workspace_id, task_id, source_component, source_index,
                raw_payload, source_columns, parsed_payload, standard_detail_id,
                waybill_mode, archived_at, archived_by, status, is_deleted
            ) VALUES (?, 1, ?, 'cainiao-cnprint', ?, ?, '{"商品":"范74"}',
                      '{"product":"old"}', 99, 'old-mode', 'old-archive', 7, 'parsed', 0)
            """,
            [
                (101, 11, "1", '{"task":{"documents":[{"contents":[{"data":{"商品":"范74"}}]}]}}'),
                (102, 12, "2", '{"task":{"documents":[{"contents":[{"data":{"商品":"秒45"}}]}]}}'),
            ],
        )
        if with_true_zero_records:
            db.executemany(
                """
                INSERT INTO raw_capture_records(
                    id, workspace_id, task_id, source_component, source_index,
                    raw_payload, source_columns, parsed_payload, standard_detail_id,
                    waybill_mode, archived_at, archived_by, status, is_deleted
                ) VALUES (?, 1, ?, 'cainiao-cnprint', ?, ?, '{}',
                          '{"product":"old"}', 99, 'old-mode', 'old-archive', 7,
                          'parsed', 0)
                """,
                [
                    (6400, 64, "1", '{"task":{"documents":[{"id":64}]}}'),
                    (6500, 65, "1", '{"task":{"documents":[{"id":65}]}}'),
                    (6600, 66, "1", '{"task":{"documents":[{"id":66}]}}'),
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
        db.execute("INSERT INTO export_header_definitions VALUES (1, '业务表头')")
        db.execute("INSERT INTO print_template_configs VALUES (1, '现场模板')")
        db.execute("INSERT INTO export_records VALUES (1, '{\"old\":true}')")
        db.execute("INSERT INTO recognition_rule_pack_revisions VALUES (1, '{\"old\":true}')")
        db.executemany(
            """
            INSERT INTO tenant_fingerprint_configs(
                id, tenant_id, fingerprint_code, is_enabled, selected_fields, is_deleted
            ) VALUES (?, ?, ?, 1, ?, 0)
            """,
            [
                (1, 1, "OLD", '["unsafe"]'),
                (2, 2, "OTHER-TENANT", '["unsafe"]'),
            ],
        )
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


def synthetic_release_contract(path: Path) -> tuple[dict[str, int], str]:
    with sqlite3.connect(path) as db:
        counts = {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "products",
                "product_skus",
                "image_assets",
                "stalls",
                "product_matching_rules",
            )
        }
        rows = db.execute(
            """
            SELECT id, raw_payload, source_columns
            FROM raw_capture_records
            WHERE task_id IN (64, 65, 66)
            ORDER BY id
            """
        ).fetchall()
    counts.update(
        {
            "capture_tasks": 3,
            "raw_capture_records": len(rows),
            "recognition_rule_packs": 0,
            "recognition_rule_pack_revisions": 0,
            "tenant_fingerprint_configs": 5,
        }
    )
    return (
        counts,
        sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest(),
    )


def build_test_cold_start_database(
    source: Path,
    destination: Path,
) -> dict[str, object]:
    counts, payload_sha = synthetic_release_contract(source)
    return build_cold_start_database(
        source,
        destination,
        task_ids=list(TRUE_ZERO_TASK_IDS),
        _expected_counts=counts,
        _expected_raw_payload_sha256=payload_sha,
    )


def test_build_cold_start_database_preserves_inputs_and_scrubs_results(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source, with_true_zero_records=True)
    source_hash = sha256_file(source)
    _counts, payload_hash = synthetic_release_contract(source)

    manifest = build_test_cold_start_database(source, destination)

    assert sha256_file(source) == source_hash
    assert raw_payload_digest(destination) == payload_hash
    assert manifest["integrity_check"] == "ok"
    assert manifest["foreign_key_check"] == "ok"
    assert manifest["source_sha256"] == source_hash
    assert manifest["tenant_id"] == 1
    assert manifest["fingerprint_configs"] == dict(
        sorted(TRUE_ZERO_FINGERPRINT_FIELDS.items())
    )
    with sqlite3.connect(destination) as db:
        db.execute("PRAGMA foreign_keys = ON")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT username FROM users").fetchone()[0] == "Administrator"
        assert db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM product_skus").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM image_assets").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM product_matching_rules").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM raw_capture_records").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM recognition_rule_packs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM standard_details").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM export_records").fetchone()[0] == 0
        assert db.execute("SELECT name FROM export_header_definitions").fetchone()[0] == "业务表头"
        assert db.execute("SELECT name FROM print_template_configs").fetchone()[0] == "现场模板"
        assert db.execute("SELECT COUNT(*) FROM recognition_rule_pack_revisions").fetchone()[0] == 0
        collector = db.execute(
            """
            SELECT token_hash, is_enabled, online_status, last_heartbeat_at, status_payload
            FROM collectors
            """
        ).fetchone()
        assert collector == (None, 0, "offline", None, None)
        derived = db.execute(
            """
            SELECT status, parsed_payload, standard_detail_id, waybill_mode,
                   archived_at, archived_by
            FROM raw_capture_records ORDER BY id
            """
        ).fetchall()
        assert derived == [
            ("pending", None, None, None, None, None),
            ("pending", None, None, None, None, None),
            ("pending", None, None, None, None, None),
        ]
        configs = db.execute(
            """
            SELECT tenant_id, fingerprint_code, selected_fields
            FROM tenant_fingerprint_configs
            ORDER BY fingerprint_code
            """
        ).fetchall()
        assert configs == [
            (1, code, json.dumps(fields, ensure_ascii=False))
            for code, fields in sorted(TRUE_ZERO_FINGERPRINT_FIELDS.items())
        ]


def test_build_cold_start_database_keeps_only_selected_completed_tasks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source)
    with sqlite3.connect(source) as db:
        db.executemany(
            "INSERT INTO capture_tasks(id, workspace_id, status, is_deleted) VALUES (?, 1, ?, 0)",
            [(63, "running"), (67, "completed")],
        )
        db.executemany(
            "INSERT INTO capture_batches(id, task_id) VALUES (?, ?)",
            [(640, 64), (650, 65), (660, 66), (670, 67)],
        )
        duplicate_payload = '{"task":{"documents":[{"contents":[{"data":{"ITEM_INFO":"same"}}]}]}}'
        db.executemany(
            """
            INSERT INTO raw_capture_records(
                id, workspace_id, task_id, source_component, source_index,
                raw_payload, source_columns, parsed_payload, standard_detail_id,
                waybill_mode, archived_at, archived_by, status, is_deleted
            ) VALUES (?, 1, ?, 'cainiao-cnprint', ?, ?, '{}', NULL, NULL, NULL,
                      NULL, NULL, 'parsed', 0)
            """,
            [
                (6401, 64, "1", duplicate_payload),
                (6402, 64, "2", duplicate_payload),
                (6501, 65, "1", '{"task":{"documents":[]}}'),
                (6601, 66, "1", '{"task":{"documents":[]}}'),
                (6701, 67, "1", '{"task":{"documents":[]}}'),
            ],
        )
        db.commit()

    manifest = build_test_cold_start_database(source, destination)

    assert manifest["selected_task_ids"] == [64, 65, 66]
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT id FROM capture_tasks ORDER BY id").fetchall() == [
            (64,),
            (65,),
            (66,),
        ]
        assert db.execute("SELECT id, task_id FROM capture_batches ORDER BY id").fetchall() == [
            (640, 64),
            (650, 65),
            (660, 66),
        ]
        rows = db.execute(
            "SELECT id, task_id, raw_payload FROM raw_capture_records ORDER BY id"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            (6401, 64),
            (6402, 64),
            (6501, 65),
            (6601, 66),
        ]
        assert rows[0][2] == rows[1][2]
        assert db.execute("SELECT COUNT(*) FROM recognition_rule_packs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM recognition_rule_pack_revisions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM export_records").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM export_header_definitions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM print_template_configs").fetchone()[0] == 1


def test_build_cold_start_database_rejects_noncompleted_selected_task(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE capture_tasks SET status = 'running' WHERE id = 66")
        db.commit()

    with pytest.raises(ValueError, match="not completed"):
        build_test_cold_start_database(source, destination)

    assert not destination.exists()


def test_build_cold_start_database_rejects_unknown_nonempty_table(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source, with_unknown_table=True)

    with pytest.raises(ValueError, match="future_derived_results"):
        build_test_cold_start_database(source, destination)

    assert not destination.exists()


def test_build_cold_start_database_never_overwrites_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source)
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        build_test_cold_start_database(source, destination)

    assert destination.read_bytes() == b"keep"


@pytest.mark.parametrize("task_ids", [[64, 65], [64, 65, 66, 66]])
def test_true_zero_build_rejects_wrong_task_set(
    tmp_path: Path,
    task_ids: list[int],
) -> None:
    source = tmp_path / "source.db"
    create_source_database(source)

    with pytest.raises(ValueError, match="64, 65, and 66"):
        build_cold_start_database(
            source,
            tmp_path / "cold.db",
            task_ids=task_ids,
        )


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("collectors", "status_payload"),
        ("raw_capture_records", "archived_by"),
    ],
)
def test_true_zero_build_rejects_missing_scrub_columns(
    tmp_path: Path,
    table: str,
    column: str,
) -> None:
    source = tmp_path / "source.db"
    create_source_database(source)
    with sqlite3.connect(source) as db:
        db.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        db.commit()

    with pytest.raises(ValueError, match=column):
        build_test_cold_start_database(source, tmp_path / "cold.db")


@pytest.mark.parametrize("mismatch", ["count", "hash"])
def test_true_zero_build_rejects_release_count_or_hash_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "cold.db"
    create_source_database(source, with_true_zero_records=True)
    counts, payload_sha = synthetic_release_contract(source)
    if mismatch == "count":
        counts["raw_capture_records"] += 1
    else:
        payload_sha = "0" * 64

    with pytest.raises(ValueError, match="count mismatch|payload hash mismatch"):
        build_cold_start_database(
            source,
            destination,
            task_ids=list(TRUE_ZERO_TASK_IDS),
            _expected_counts=counts,
            _expected_raw_payload_sha256=payload_sha,
        )

    assert not destination.exists()


def test_asset_manifest_and_volume_verifier_are_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    cold = tmp_path / "cold.db"
    assets = tmp_path / "workspaces"
    create_source_database(source, with_true_zero_records=True)
    counts, payload_sha = synthetic_release_contract(source)
    build_test_cold_start_database(source, cold)
    (assets / "nested").mkdir(parents=True)
    (assets / "b.txt").write_bytes(b"second")
    (assets / "nested" / "a.txt").write_bytes(b"first")

    expected_digest = sha256()
    for relative_path, content in [
        ("b.txt", b"second"),
        ("nested/a.txt", b"first"),
    ]:
        expected_digest.update(relative_path.encode())
        expected_digest.update(b"\0")
        expected_digest.update(sha256(content).digest())
    expected_assets = {
        "asset_count": 2,
        "asset_manifest_sha256": expected_digest.hexdigest(),
    }
    assert asset_manifest(assets) == expected_assets

    monkeypatch.setattr(validation_dataset, "TRUE_ZERO_RELEASE_COUNTS", counts)
    monkeypatch.setattr(
        validation_dataset,
        "TRUE_ZERO_RAW_PAYLOAD_SHA256",
        payload_sha,
    )
    monkeypatch.setattr(validation_dataset, "TRUE_ZERO_ASSET_COUNT", 2)
    monkeypatch.setattr(
        validation_dataset,
        "TRUE_ZERO_ASSET_MANIFEST_SHA256",
        expected_assets["asset_manifest_sha256"],
    )
    assert validation_dataset.verify_true_zero_volume(cold, assets)[
        "asset_manifest_sha256"
    ] == expected_assets["asset_manifest_sha256"]

    monkeypatch.setattr(
        validation_dataset,
        "TRUE_ZERO_ASSET_MANIFEST_SHA256",
        "0" * 64,
    )
    with pytest.raises(ValueError, match="asset manifest mismatch"):
        validation_dataset.verify_true_zero_volume(cold, assets)


def test_true_zero_release_rejects_old_derived_rows(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    cold = tmp_path / "cold.db"
    create_source_database(source, with_true_zero_records=True)
    counts, payload_sha = synthetic_release_contract(source)
    build_test_cold_start_database(source, cold)
    with sqlite3.connect(cold) as db:
        db.execute(
            "INSERT INTO standard_details VALUES (99, 1, ?, NULL, 0)",
            (json.dumps({"product": "old-derived-row"}),),
        )
        db.commit()

    with sqlite3.connect(cold) as db, pytest.raises(
        ValueError,
        match="cleared tables are not empty.*standard_details",
    ):
        validation_dataset._verify_true_zero_release(
            db,
            expected_counts=counts,
            expected_raw_payload_sha256=payload_sha,
        )


def test_true_zero_cli_rejects_parser_and_oracle_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ai_validation_dataset.py",
            "--source-db",
            str(tmp_path / "source.db"),
            "--cold-db",
            str(tmp_path / "cold.db"),
            "--true-zero",
            "--parser-url",
            "http://127.0.0.1:8010",
        ],
    )

    with pytest.raises(SystemExit) as error:
        validation_dataset.main()

    assert error.value.code == 2
    assert not (tmp_path / "cold.db").exists()


def true_zero_script_environment() -> dict[str, str]:
    return {
        **os.environ,
        "AI_RECOGNITION_INTERNAL_TOKEN": "test-only",
        "VALIDATION_APP_VERSION": "candidate",
        "VALIDATION_BACKEND_IMAGE": "test/backend:candidate",
        "VALIDATION_PARSER_IMAGE": "test/parser:candidate",
        "VALIDATION_AI_IMAGE": "test/ai:candidate",
        "VALIDATION_UI_IMAGE": "test/ui:candidate",
        "VALIDATION_PLATFORM_VOLUME":
            "cargo-platform-validation-zero-platform-candidate",
        "VALIDATION_REDIS_VOLUME":
            "cargo-platform-validation-zero-redis-candidate",
        "VALIDATION_AI_SESSION_VOLUME":
            "cargo-platform-validation-zero-ai-candidate",
        "ROLLBACK_APP_VERSION": "rollback",
        "ROLLBACK_BACKEND_IMAGE": "test/backend:rollback",
        "ROLLBACK_PARSER_IMAGE": "test/parser:rollback",
        "ROLLBACK_AI_IMAGE": "test/ai:rollback",
        "ROLLBACK_UI_IMAGE": "test/ui:rollback",
        "ROLLBACK_PLATFORM_VOLUME":
            "cargo-platform-validation-adaptive-data-rollback",
        "ROLLBACK_REDIS_VOLUME":
            "cargo-platform-validation-adaptive-redis-rollback",
        "ROLLBACK_AI_SESSION_VOLUME":
            "cargo-platform-validation-adaptive-ai-sessions-rollback",
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (
            {"VALIDATION_PLATFORM_VOLUME": "cargo-platform-validation-wrong"},
            "candidate platform",
        ),
        (
            {
                "VALIDATION_REDIS_VOLUME":
                    "cargo-platform-validation-zero-platform-candidate"
            },
            "six distinct volumes",
        ),
        (
            {
                "ROLLBACK_REDIS_VOLUME":
                    "cargo-platform-validation-adaptive-data-rollback"
            },
            "six distinct volumes",
        ),
    ],
)
def test_prepare_true_zero_script_rejects_wrong_or_duplicate_volume_roles(
    override: dict[str, str],
    message: str,
) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    assert pwsh is not None
    repo_root = Path(__file__).resolve().parents[2]
    environment = {
        **true_zero_script_environment(),
        **override,
    }

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(repo_root / "scripts" / "prepare_true_zero_validation.ps1"),
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert message in f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    ("fake_docker_mode", "message"),
    [
        ("mounted", "must not be mounted by any container"),
        ("old-file", "must be empty"),
    ],
)
def test_prepare_true_zero_script_rejects_used_candidate_scratch_volumes(
    tmp_path: Path,
    fake_docker_mode: str,
    message: str,
) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    assert pwsh is not None
    repo_root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker.cmd").write_text(
        "\r\n".join(
            [
                "@echo off",
                "set \"all_args=%*\"",
                "if \"%FAKE_DOCKER_MODE%\"==\"mounted\" (",
                "  echo %all_args% | findstr /C:\"ps --all --quiet --filter "
                "volume=cargo-platform-validation-zero-ai-candidate\" >nul",
                "  if not errorlevel 1 (",
                "    echo stale-container",
                "    exit /b 0",
                "  )",
                ")",
                "if \"%FAKE_DOCKER_MODE%\"==\"old-file\" (",
                "  echo %all_args% | findstr /C:\"source="
                "cargo-platform-validation-zero-ai-candidate\" >nul",
                "  if not errorlevel 1 (",
                "    echo old-session.json",
                "    exit /b 9",
                "  )",
                ")",
                "exit /b 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    environment = {
        **true_zero_script_environment(),
        "FAKE_DOCKER_MODE": fake_docker_mode,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(repo_root / "scripts" / "prepare_true_zero_validation.ps1"),
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert message in f"{result.stdout}\n{result.stderr}"


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
