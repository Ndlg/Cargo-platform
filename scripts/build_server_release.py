from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import stat
import sys
import tarfile
import zipfile
from pathlib import Path


SOURCE_PATHS = (
    "SERVER-DEPLOYMENT.md",
    "deploy.env.example",
    "docker-compose.release.yml",
    "scripts/deploy_business_containers.ps1",
    "scripts/deploy_server.sh",
    "scripts/sqlite_snapshot.py",
    "scripts/sqlite_volume_snapshot.ps1",
)
SHELL_PATH = "scripts/deploy_server.sh"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_inputs(root: Path, version: str, git_sha: str) -> str:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Version must be a semantic version such as 1.0.1.")
    if not GIT_SHA_PATTERN.fullmatch(git_sha):
        raise ValueError("Git SHA must be a full 40-character hexadecimal commit id.")

    env_path = root / "deploy.env.example"
    if not env_path.is_file():
        raise ValueError(f"Required server release input is missing: {env_path}")
    template_version = next(
        (
            line.split("=", 1)[1].strip()
            for line in env_path.read_text(encoding="utf-8-sig").splitlines()
            if line.startswith("CARGO_PLATFORM_VERSION=")
        ),
        None,
    )
    if template_version != version:
        raise ValueError(
            "deploy.env.example CARGO_PLATFORM_VERSION "
            f"{template_version!r} does not match requested version {version!r}."
        )
    return git_sha.lower()


def payload_files(root: Path, version: str, git_sha: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for relative_path in SOURCE_PATHS:
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"Required server release input is missing: {path}")
        files[relative_path] = path.read_bytes()
    files["VERSION.txt"] = f"release_version={version}\ngit_sha={git_sha}\n".encode()
    return files


def file_mode(path: str) -> int:
    return 0o755 if path == SHELL_PATH else 0o644


def manifest_bytes(
    files: dict[str, bytes],
    *,
    version: str,
    git_sha: str,
    archive_root: str,
) -> bytes:
    manifest = {
        "schema_version": 1,
        "release_version": version,
        "git_sha": git_sha,
        "archive_root": archive_root,
        "files": [
            {
                "path": path,
                "size": len(files[path]),
                "sha256": sha256_bytes(files[path]),
                "mode": f"{file_mode(path):04o}",
            }
            for path in sorted(files)
        ],
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()


def write_zip(path: Path, archive_root: str, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for relative_path in sorted(files):
            mode = file_mode(relative_path)
            member = zipfile.ZipInfo(
                f"{archive_root}/{relative_path}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            member.compress_type = zipfile.ZIP_DEFLATED
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(member, files[relative_path])


def write_tar_gz(path: Path, archive_root: str, files: dict[str, bytes]) -> None:
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for relative_path in sorted(files):
                    content = files[relative_path]
                    member = tarfile.TarInfo(f"{archive_root}/{relative_path}")
                    member.size = len(content)
                    member.mode = file_mode(relative_path)
                    member.mtime = 0
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    archive.addfile(member, io.BytesIO(content))


def build_release(root: Path, version: str, git_sha: str, output_dir: Path) -> list[Path]:
    root = root.resolve()
    git_sha = validate_inputs(root, version, git_sha)
    files = payload_files(root, version, git_sha)
    archive_root = f"cargo-platform-server-{version}"
    manifest = manifest_bytes(
        files,
        version=version,
        git_sha=git_sha,
        archive_root=archive_root,
    )
    archive_files = {**files, "server-manifest.json": manifest}

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{archive_root}.zip"
    tar_path = output_dir / f"{archive_root}.tar.gz"
    manifest_path = output_dir / "server-manifest.json"
    checksums_path = output_dir / "SHA256SUMS-server.txt"
    manifest_path.write_bytes(manifest)
    write_zip(zip_path, archive_root, archive_files)
    write_tar_gz(tar_path, archive_root, archive_files)

    checksum_paths = sorted((zip_path, tar_path, manifest_path), key=lambda item: item.name)
    checksums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in checksum_paths),
        encoding="utf-8",
        newline="\n",
    )
    return [zip_path, tar_path, manifest_path, checksums_path]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Cargo Platform server release archives.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        outputs = build_release(args.root, args.version, args.git_sha, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
