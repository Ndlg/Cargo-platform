from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4


RESTORE_CONFIRMATION = "RESTORE_STOPPED_DATABASE"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"SQLite database does not exist: {path}")
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def inspect_database(path: Path) -> dict[str, object]:
    with closing(readonly_connection(path)) as connection:
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if integrity_rows != ["ok"]:
        raise ValueError(f"SQLite integrity check failed: {integrity_rows[:3]}")
    return {
        "integrity_check": "ok",
        "schema_version": schema_version,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def create_snapshot(source: Path, destination: Path) -> dict[str, object]:
    if destination.exists():
        raise ValueError(f"Refusing to overwrite snapshot: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with closing(readonly_connection(source)) as source_db, closing(sqlite3.connect(temporary)) as target_db:
            source_db.backup(target_db)
        details = inspect_database(temporary)
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {**details, "path": str(destination.resolve())}


def backup(source: Path, destination: Path) -> dict[str, object]:
    return {
        "action": "backup",
        "source": str(source.resolve()),
        **create_snapshot(source, destination),
    }


def restore(
    backup_path: Path,
    target: Path,
    *,
    expected_sha256: str,
    confirmation: str,
) -> dict[str, object]:
    if confirmation != RESTORE_CONFIRMATION:
        raise ValueError(f"Restore requires --confirm {RESTORE_CONFIRMATION}.")
    backup_details = inspect_database(backup_path)
    if not hmac.compare_digest(str(backup_details["sha256"]).casefold(), expected_sha256.casefold()):
        raise ValueError("Backup SHA-256 does not match --expected-sha256; target was not changed.")

    target.parent.mkdir(parents=True, exist_ok=True)
    pre_restore: dict[str, object] | None = None
    if target.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safety_path = target.with_name(f"{target.stem}.pre-restore-{timestamp}{target.suffix}")
        pre_restore = create_snapshot(target, safety_path)

    temporary = target.with_name(f".{target.name}.{uuid4().hex}.restore")
    try:
        create_snapshot(backup_path, temporary)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    restored = inspect_database(target)
    return {
        "action": "restore",
        "target": str(target.resolve()),
        **restored,
        "pre_restore_backup": pre_restore["path"] if pre_restore else None,
        "pre_restore_sha256": pre_restore["sha256"] if pre_restore else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verified online SQLite backup and stopped-database restore.")
    commands = parser.add_subparsers(dest="action", required=True)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("destination", type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("target", type=Path)
    restore_parser.add_argument("--expected-sha256", required=True)
    restore_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action == "backup":
            result = backup(args.source, args.destination)
        else:
            result = restore(
                args.backup,
                args.target,
                expected_sha256=args.expected_sha256,
                confirmation=args.confirm,
            )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
