from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_server_release.py"
VERSION = "1.0.1"
GIT_SHA = "cbdde2f9c672930724d28a4ddc9ab28eb45a18f1"
PACKAGE_ROOT = f"cargo-platform-server-{VERSION}"

SOURCE_FILES = {
    "SERVER-DEPLOYMENT.md": b"# Server deployment\n",
    "deploy.env.example": (
        f"CARGO_PLATFORM_VERSION={VERSION}\n"
        "SECRET_KEY=\n"
        "COLLECTOR_TOKEN_HASH_KEY=\n"
        "INITIAL_SETUP_TOKEN=\n"
    ).encode(),
    "docker-compose.release.yml": (
        "services:\n"
        "  backend:\n"
        "    image: ghcr.io/ndlg/cargo-platform-backend:${CARGO_PLATFORM_VERSION}\n"
    ).encode(),
    "scripts/deploy_business_containers.ps1": b"param([string]$Version)\n",
    "scripts/deploy_server.sh": b"#!/bin/sh\nset -eu\necho deploy\n",
    "scripts/sqlite_snapshot.py": b"print('snapshot')\n",
    "scripts/sqlite_volume_snapshot.ps1": b"param([string]$VolumeName)\n",
}
VERSION_FILE = f"release_version={VERSION}\ngit_sha={GIT_SHA}\n".encode()
ARCHIVE_PATHS = set(SOURCE_FILES) | {"VERSION.txt", "server-manifest.json"}
RUNTIME_FILES = {
    ".env": b"SECRET_KEY=production-secret\n",
    "collector-runtime.json": b'{"token":"runtime-secret"}\n',
    "logs/backend.log": b"runtime log\n",
    "storage/workspaces/1/cargo-platform.db": b"runtime database\n",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_server_source(root: Path) -> None:
    for relative_path, content in {**SOURCE_FILES, **RUNTIME_FILES}.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def run_builder(
    source_root: Path,
    output_dir: Path,
    *,
    version: str = VERSION,
    git_sha: str = GIT_SHA,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--root",
            str(source_root),
            "--version",
            version,
            "--git-sha",
            git_sha,
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def build_fixture(tmp_path: Path) -> Path:
    source_root = tmp_path / "repo"
    output_dir = tmp_path / "release"
    write_server_source(source_root)
    result = run_builder(source_root, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    return output_dir


def zip_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            member.filename: archive.read(member)
            for member in archive.infolist()
            if not member.is_dir()
        }


def tar_files(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            files[member.name] = extracted.read()
    return files


def archive_name(relative_path: str) -> str:
    return f"{PACKAGE_ROOT}/{relative_path}"


def test_builds_zip_and_tar_with_exact_server_files_only(tmp_path: Path) -> None:
    output_dir = build_fixture(tmp_path)
    expected_outputs = {
        f"cargo-platform-server-{VERSION}.zip",
        f"cargo-platform-server-{VERSION}.tar.gz",
        "server-manifest.json",
        "SHA256SUMS-server.txt",
    }
    assert {path.name for path in output_dir.iterdir()} == expected_outputs

    zip_contents = zip_files(output_dir / f"cargo-platform-server-{VERSION}.zip")
    tar_contents = tar_files(output_dir / f"cargo-platform-server-{VERSION}.tar.gz")
    expected_members = {archive_name(path) for path in ARCHIVE_PATHS}
    assert set(zip_contents) == expected_members
    assert set(tar_contents) == expected_members
    assert zip_contents == tar_contents

    for relative_path, expected_content in SOURCE_FILES.items():
        assert zip_contents[archive_name(relative_path)] == expected_content
    assert zip_contents[archive_name("VERSION.txt")] == VERSION_FILE
    assert b"production-secret" not in b"".join(zip_contents.values())
    assert b"runtime-secret" not in b"".join(zip_contents.values())


def test_preserves_deploy_shell_executable_mode_in_both_archives(tmp_path: Path) -> None:
    output_dir = build_fixture(tmp_path)
    shell_path = archive_name("scripts/deploy_server.sh")

    with zipfile.ZipFile(output_dir / f"cargo-platform-server-{VERSION}.zip") as archive:
        member = archive.getinfo(shell_path)
        assert member.create_system == 3
        assert (member.external_attr >> 16) & 0o777 == 0o755

    with tarfile.open(output_dir / f"cargo-platform-server-{VERSION}.tar.gz", "r:gz") as archive:
        assert archive.getmember(shell_path).mode & 0o777 == 0o755


def test_manifest_and_sha256sums_verify_every_release_output(tmp_path: Path) -> None:
    output_dir = build_fixture(tmp_path)
    manifest_path = output_dir / "server-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    payload_files = {**SOURCE_FILES, "VERSION.txt": VERSION_FILE}
    expected_manifest_files = [
        {
            "path": path,
            "size": len(payload_files[path]),
            "sha256": sha256_bytes(payload_files[path]),
            "mode": "0755" if path == "scripts/deploy_server.sh" else "0644",
        }
        for path in sorted(payload_files)
    ]
    assert manifest == {
        "schema_version": 1,
        "release_version": VERSION,
        "git_sha": GIT_SHA,
        "archive_root": PACKAGE_ROOT,
        "files": expected_manifest_files,
    }

    zip_contents = zip_files(output_dir / f"cargo-platform-server-{VERSION}.zip")
    tar_contents = tar_files(output_dir / f"cargo-platform-server-{VERSION}.tar.gz")
    assert zip_contents[archive_name("server-manifest.json")] == manifest_bytes
    assert tar_contents[archive_name("server-manifest.json")] == manifest_bytes

    checksum_lines = (output_dir / "SHA256SUMS-server.txt").read_text(encoding="utf-8").splitlines()
    checksums: dict[str, str] = {}
    for line in checksum_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        assert match is not None, line
        checksums[match.group(2)] = match.group(1)
    expected_checksum_files = {
        f"cargo-platform-server-{VERSION}.zip",
        f"cargo-platform-server-{VERSION}.tar.gz",
        "server-manifest.json",
    }
    assert set(checksums) == expected_checksum_files
    assert checksums == {
        name: sha256_file(output_dir / name)
        for name in expected_checksum_files
    }


@pytest.mark.parametrize(
    ("version", "git_sha", "expected_error"),
    [
        ("1.0", GIT_SHA, "semantic version"),
        (VERSION, "g" * 40, "40-character hexadecimal"),
    ],
)
def test_rejects_invalid_version_or_git_sha(
    tmp_path: Path,
    version: str,
    git_sha: str,
    expected_error: str,
) -> None:
    source_root = tmp_path / "repo"
    output_dir = tmp_path / "release"
    write_server_source(source_root)

    result = run_builder(source_root, output_dir, version=version, git_sha=git_sha)

    assert result.returncode != 0
    assert expected_error in (result.stdout + result.stderr).casefold()
    assert not output_dir.exists() or not any(output_dir.iterdir())


def test_rejects_version_mismatch_with_environment_template(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    output_dir = tmp_path / "release"
    write_server_source(source_root)
    (source_root / "deploy.env.example").write_text(
        "CARGO_PLATFORM_VERSION=9.9.9\n",
        encoding="utf-8",
    )

    result = run_builder(source_root, output_dir)

    assert result.returncode != 0
    error = (result.stdout + result.stderr).casefold()
    assert "deploy.env.example" in error
    assert "does not match" in error
    assert VERSION in error
    assert not output_dir.exists() or not any(output_dir.iterdir())
