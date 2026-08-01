from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_customer_delivery_audit_uses_export_coverage_partition() -> None:
    source = (ROOT / "scripts" / "customer_delivery_audit.ps1").read_text(encoding="utf-8")

    assert 'Name "normal_export_count"' in source
    assert 'Name "exception_export_count"' in source
    assert "Report preview rows (" not in source
    assert "-and (Has-BusinessValue $salesAttr1)" not in source


def test_sqlite_volume_restore_refuses_running_volume() -> None:
    source = (ROOT / "scripts" / "sqlite_volume_snapshot.ps1").read_text(encoding="utf-8")

    assert "sqlite_snapshot.py" in source
    assert 'docker ps -q --filter "volume=$VolumeName"' in source
    assert "RESTORE_STOPPED_DATABASE" in source


def test_business_deploy_takes_verified_snapshot_before_recreate() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    backup_index = source.index("sqlite_volume_snapshot.ps1")
    recreate_index = source.index('compose @composeFiles up -d')
    assert backup_index < recreate_index
