import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sqlite_snapshot.py"


def _seed(path: Path, value: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        connection.commit()


def _value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("SELECT value FROM sample").fetchone()[0])


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_online_backup_and_verified_restore_keep_a_pre_restore_snapshot(tmp_path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    _seed(source, "before-upgrade")
    _seed(target, "after-upgrade")

    backed_up = _run("backup", source, backup)
    assert backed_up.returncode == 0, backed_up.stderr
    backup_result = json.loads(backed_up.stdout)
    assert backup_result["integrity_check"] == "ok"
    assert len(backup_result["sha256"]) == 64

    restored = _run(
        "restore",
        backup,
        target,
        "--expected-sha256",
        backup_result["sha256"],
        "--confirm",
        "RESTORE_STOPPED_DATABASE",
    )
    assert restored.returncode == 0, restored.stderr
    restore_result = json.loads(restored.stdout)
    assert _value(target) == "before-upgrade"
    safety_backup = Path(restore_result["pre_restore_backup"])
    assert safety_backup.exists()
    assert _value(safety_backup) == "after-upgrade"


def test_restore_rejects_wrong_hash_without_touching_target(tmp_path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    _seed(backup, "backup")
    _seed(target, "current")

    result = _run(
        "restore",
        backup,
        target,
        "--expected-sha256",
        "0" * 64,
        "--confirm",
        "RESTORE_STOPPED_DATABASE",
    )

    assert result.returncode != 0
    assert "SHA-256" in result.stderr
    assert _value(target) == "current"
