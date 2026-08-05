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
    assert "Join-Path $resolvedBackupDirectory $fileName" in source


def test_development_compose_keeps_backend_and_parser_on_one_version() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")

    assert source.count("APP_VERSION: ${CARGO_PLATFORM_VERSION:-1.0.0-rc.1}") == 2


def test_business_deploy_takes_verified_snapshot_before_recreate() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    backup_index = source.index("sqlite_volume_snapshot.ps1")
    recreate_index = source.index('compose @composeFiles up -d')
    assert backup_index < recreate_index
    assert "pull @services" in source
    assert "--wait" in source
    assert "restoring the previous four images" in source
    assert "composeConfig.services.backend.environment.APP_VERSION" in source
    assert "previous_app_versions" in source
    assert "previous_image_ids" in source
    assert "target_image_ids" in source
    assert "Existing release is incomplete" in source
    assert "rollback verification failed" in source
    assert "Production data volume cargo-platform-data does not exist" in source
    assert "Production database cargo-platform.db does not exist" in source
    assert "docker volume create cargo-platform-data" not in source
    assert "disable: true" not in source
    assert "http://127.0.0.1:8000/api/v1/health" in source


def test_release_compose_requires_one_version_and_runtime_guards() -> None:
    source = (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8-sig")

    assert source.count("${CARGO_PLATFORM_VERSION:?set CARGO_PLATFORM_VERSION}") == 6
    assert ":latest" not in source
    assert source.count("healthcheck:") == 4
    assert source.count("logging: *default-logging") == 4
    assert "external: true" in source


def test_release_builds_four_immutable_images_after_quality_gate() -> None:
    script = (ROOT / "scripts" / "release_images.ps1").read_text(encoding="utf-8-sig")
    workflow = (ROOT / ".github" / "workflows" / "release-images.yml").read_text(encoding="utf-8-sig")

    assert "cargo-platform-waybill-parser" in script
    assert ":latest" not in script
    assert "Parameter(Mandatory = $true)" in script
    assert "needs: [version, collector, quality, immutable-tags]" in workflow
    assert "group: cargo-platform-release-images" in workflow
    assert 'VERSION="${{ github.event.inputs.version }}"' not in workflow
    assert "REQUESTED_VERSION:" in workflow
    assert "Refusing to overwrite existing release tag" in script
    assert "Refusing to overwrite existing release tag" in workflow
    assert "manifest unknown|no such manifest" in script
    assert "manifest unknown|no such manifest" in workflow
    assert "Unable to verify release tag" in script
    assert "Unable to verify release tag" in workflow
    assert "python -m pytest backend/tests -q" in workflow
    assert "npm audit --omit=dev --audit-level=high" in workflow
    assert ":latest" not in workflow


def test_release_dependencies_exclude_retired_jose_and_vulnerable_pillow() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "python-jose" not in requirements.casefold()
    assert "passlib" not in requirements.casefold()
    assert "pytest" not in requirements.casefold()
    assert "pillow>=12.3,<13.0" in requirements.casefold()
