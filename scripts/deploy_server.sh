#!/bin/sh
set -eu

case "$PATH" in
    *:*) PATH="${PATH%%:*}:/usr/bin:/bin:/usr/sbin:/sbin:${PATH#*:}" ;;
    *) PATH="$PATH:/usr/bin:/bin:/usr/sbin:/sbin" ;;
esac
export PATH

services="waybill-parser backend tenant-ui platform-admin-ui"
version=
backup_dir=

usage() {
    echo "Usage: $0 [--version <version>] [--backup-dir <directory>]" >&2
    exit 2
}

die() {
    echo "error: $*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            [ "$#" -ge 2 ] || usage
            version=$2
            shift 2
            ;;
        --backup-dir)
            [ "$#" -ge 2 ] || usage
            backup_dir=$2
            shift 2
            ;;
        *) usage ;;
    esac
done

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd -P)
cd "$repo_root"

release_compose="$repo_root/docker-compose.release.yml"
snapshot_script="$script_dir/sqlite_snapshot.py"
env_file="$repo_root/.env"
env_template="$repo_root/deploy.env.example"
install_marker="$repo_root/.cargo-platform-install-pending"
volume_name=cargo-platform-data

[ -f "$release_compose" ] || die "missing docker-compose.release.yml"
[ -f "$snapshot_script" ] || die "missing scripts/sqlite_snapshot.py"
[ -f "$env_template" ] || die "missing deploy.env.example"
if [ -z "$version" ]; then
    version=$(awk -F= '$1 == "CARGO_PLATFORM_VERSION" { print substr($0, index($0, "=") + 1); exit }' "$env_template")
fi
[ -n "$version" ] || die "deploy.env.example has no CARGO_PLATFORM_VERSION"
printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' || \
    die "version must be a semantic version"
command -v docker >/dev/null 2>&1 || die "Docker was not found"
command -v curl >/dev/null 2>&1 || die "curl was not found"

if [ -z "$backup_dir" ]; then
    backup_dir="$repo_root/../cargo-platform-deploy-backups"
fi
mkdir -p "$backup_dir"
backup_dir=$(CDPATH= cd "$backup_dir" && pwd -P)
chmod 700 "$backup_dir"
stamp=$(date -u +%Y%m%d-%H%M%S)-$$

compose() {
    docker compose -f "$release_compose" "$@"
}

set_env_value() {
    key=$1
    value=$2
    temporary="$env_file.tmp.$$"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$env_file" >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$env_file"
}

env_value() {
    awk -F= -v key="$1" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$env_file"
}

random_hex() {
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

restore_env() {
    [ -n "${env_backup:-}" ] || return 0
    cp "$env_backup" "$env_file" || return 1
    chmod 600 "$env_file" || return 1
}

deployment_mutex_container=
preserve_deployment_mutex=0
release_deployment_mutex() {
    if [ -n "$deployment_mutex_container" ]; then
        docker rm -f "$deployment_mutex_container" >/dev/null 2>&1 || return 1
    fi
    deployment_mutex_container=
}

release_deployment_mutex_on_exit() {
    [ "$preserve_deployment_mutex" -eq 1 ] || release_deployment_mutex || true
}

installed_container_count() {
    count=0
    for container in cargo-platform-waybill-parser cargo-platform-backend cargo-platform-tenant-ui cargo-platform-admin-ui; do
        container_id=$(docker ps -a -q --filter "name=^/$container$") || die "failed to inspect $container"
        [ -z "$container_id" ] || count=$((count + 1))
    done
    printf '%s\n' "$count"
}

cleanup_incomplete_install() {
    if ! compose rm -f -s $services >/dev/null 2>&1; then
        preserve_deployment_mutex=1
        return 1
    fi
}

restart_previous_services() {
    rollback_previous
}

expected_image_id() {
    case "$1" in
        waybill-parser) printf '%s\n' "$previous_waybill_parser_id" ;;
        backend) printf '%s\n' "$previous_backend_id" ;;
        tenant-ui) printf '%s\n' "$previous_tenant_ui_id" ;;
        platform-admin-ui) printf '%s\n' "$previous_platform_admin_ui_id" ;;
        *) return 1 ;;
    esac
}

target_image_ref() {
    case "$1" in
        waybill-parser) printf 'ghcr.io/ndlg/cargo-platform-waybill-parser:%s\n' "$version" ;;
        backend) printf 'ghcr.io/ndlg/cargo-platform-backend:%s\n' "$version" ;;
        tenant-ui) printf 'ghcr.io/ndlg/cargo-platform-tenant-ui:%s\n' "$version" ;;
        platform-admin-ui) printf 'ghcr.io/ndlg/cargo-platform-admin-ui:%s\n' "$version" ;;
        *) return 1 ;;
    esac
}

expected_target_image_id() {
    case "$1" in
        waybill-parser) printf '%s\n' "$target_waybill_parser_id" ;;
        backend) printf '%s\n' "$target_backend_id" ;;
        tenant-ui) printf '%s\n' "$target_tenant_ui_id" ;;
        platform-admin-ui) printf '%s\n' "$target_platform_admin_ui_id" ;;
        *) return 1 ;;
    esac
}

ensure_no_active_capture() {
    recovery=${1:-}
    probe_failed=0
    active_status=$(
        docker run --rm --entrypoint python \
            --mount "type=volume,src=$volume_name,dst=/data,readonly" \
            "$current_backend_image" -c \
            'import sqlite3; db=sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True); row=db.execute("SELECT status FROM capture_tasks WHERE status IN (\"collecting\", \"stopping\") LIMIT 1").fetchone(); print(row[0] if row else "")'
    ) || probe_failed=1
    if [ "$probe_failed" -eq 1 ]; then
        [ "$recovery" != restart ] || restart_previous_services || die "active capture inspection failed and previous services could not restart"
        die "failed to inspect active capture tasks"
    fi
    active_status=$(printf '%s' "$active_status" | tr -d ' \r\n')
    case "$active_status" in
        collecting|stopping)
            [ "$recovery" != restart ] || restart_previous_services || die "capture task is $active_status and previous services could not restart"
            die "capture task is $active_status; refusing deployment"
            ;;
    esac
}

ensure_expected_volume_consumers() {
    allow_backend=${1:-}
    consumers=$(docker ps -q --no-trunc --filter "volume=$volume_name") || return 1
    for consumer in $consumers; do
        consumer_name=$(docker inspect --format '{{.Name}}' "$consumer") || return 1
        case "$consumer_name" in
            /cargo-platform-deploy-db-lock) ;;
            /cargo-platform-backend) [ "$allow_backend" = backend ] || {
                echo "unexpected running container uses $volume_name: ${consumer_name#/}" >&2
                return 1
            } ;;
            *)
                echo "unexpected running container uses $volume_name: ${consumer_name#/}" >&2
                return 1
                ;;
        esac
    done
}

database_integrity_ok() {
    backend_container=$(compose ps -q backend) || return 1
    [ -n "$backend_container" ] || return 1
    backend_image=$(docker inspect --format '{{.Image}}' "$backend_container") || return 1
    integrity=$(
        docker run --rm --entrypoint python \
            --mount "type=volume,src=$volume_name,dst=/data,readonly" \
            "$backend_image" -c \
            'import sqlite3; db=sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True); assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"; [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() for table in ("capture_tasks", "raw_capture_records", "products")]; print("ok")'
    ) || return 1
    [ "$(printf '%s' "$integrity" | tr -d ' \r\n')" = ok ]
}

unset CARGO_PLATFORM_VERSION SECRET_KEY COLLECTOR_TOKEN_HASH_KEY \
    COLLECTOR_TOKEN_PREVIOUS_HASH_KEY INITIAL_SETUP_TOKEN CORS_ORIGINS || true

mutex_ref=$(target_image_ref backend)
docker pull "$mutex_ref" >/dev/null || die "failed to pull the backend image required for the deployment mutex"
mutex_image=$(docker image inspect --format '{{.Id}}' "$mutex_ref") || die "failed to inspect the backend image required for the deployment mutex"
deployment_mutex_container=cargo-platform-deploy-mutex
if ! docker run --rm -d --name "$deployment_mutex_container" --entrypoint python \
    "$mutex_image" -c 'import threading; threading.Event().wait()' >/dev/null; then
    deployment_mutex_container=
    die "failed to acquire the deployment mutex; another deployment may be running (remove stale lock only after checking: docker rm -f cargo-platform-deploy-mutex)"
fi
trap release_deployment_mutex_on_exit EXIT

resume_install=0
create_install_state=0
installed_count=$(installed_container_count)
if docker volume inspect "$volume_name" >/dev/null 2>&1; then
    [ -f "$env_file" ] || die "existing data volume has no .env; refusing deployment"
    if [ -f "$install_marker" ]; then
        upgrade=0
        resume_install=1
    else
        upgrade=1
    fi
else
    upgrade=0
    [ "$installed_count" -eq 0 ] || die "existing containers have no $volume_name; refusing deployment"
    if [ -f "$install_marker" ]; then
        create_install_state=1
    else
        [ ! -e "$env_file" ] || die "$volume_name is missing and .env was not created by an unfinished installation; refusing an empty replacement"
        create_install_state=1
    fi
fi

if [ "$resume_install" -eq 1 ] && [ "$installed_count" -gt 0 ]; then
    die "unfinished first installation still has containers; refusing automatic teardown (confirm collection is idle, keep the marker and data volume, remove only the four service containers, then retry the same version)"
fi

if [ "$create_install_state" -eq 1 ]; then
    umask 077
    if [ ! -f "$install_marker" ]; then
        : >"$install_marker"
        chmod 600 "$install_marker"
    fi
    if [ ! -e "$env_file" ]; then
        cp "$env_template" "$env_file"
    fi
fi

if [ "$upgrade" -eq 0 ]; then
    [ -f "$env_file" ] || die ".env is not a regular file"
    chmod 600 "$env_file"
    if [ "$resume_install" -eq 1 ]; then
        pending_version=$(env_value CARGO_PLATFORM_VERSION)
        [ -n "$pending_version" ] || die "pending first installation has no CARGO_PLATFORM_VERSION; refusing an unsafe retry"
        [ "$pending_version" = "$version" ] || die "pending first installation has data for $pending_version; retry version $pending_version before upgrading"
    fi
    set_env_value CARGO_PLATFORM_VERSION "$version"
    for key in SECRET_KEY COLLECTOR_TOKEN_HASH_KEY INITIAL_SETUP_TOKEN; do
        if [ -z "$(env_value "$key")" ]; then
            secret=$(random_hex)
            [ "${#secret}" -eq 64 ] || die "failed to generate $key"
            set_env_value "$key" "$secret"
        fi
    done
fi

export CARGO_PLATFORM_VERSION=$version
compose config --quiet || die "Docker Compose configuration is invalid"
if [ "$resume_install" -eq 1 ]; then
    cleanup_incomplete_install || die "failed to clean incomplete first-install containers"
fi

rollback_file=
env_backup=
current_backend_image=
previous_waybill_parser_id=
previous_backend_id=
previous_tenant_ui_id=
previous_platform_admin_ui_id=
previous_version=
target_waybill_parser_id=
target_backend_id=
target_tenant_ui_id=
target_platform_admin_ui_id=
if [ "$upgrade" -eq 1 ]; then
    env_backup="$backup_dir/.env-$stamp"
    cp "$env_file" "$env_backup"
    chmod 600 "$env_backup"
    previous_version=$(env_value CARGO_PLATFORM_VERSION)
    [ -n "$previous_version" ] || die "existing .env has no CARGO_PLATFORM_VERSION"

    rollback_file="$backup_dir/docker-compose.rollback-$stamp.yml"
    rollback_tmp="$rollback_file.tmp"
    echo "services:" >"$rollback_tmp"
    for service in $services; do
        container_id=$(compose ps -a -q "$service") || die "failed to inspect $service"
        [ -n "$container_id" ] || die "existing release is missing $service"
        image_id=$(docker inspect --format '{{.Image}}' "$container_id") || die "failed to inspect image for $service"
        [ -n "$image_id" ] || die "existing release has no image ID for $service"
        {
            echo "  $service:"
            echo "    image: $image_id"
        } >>"$rollback_tmp"
        case "$service" in
            waybill-parser) previous_waybill_parser_id=$image_id ;;
            backend) previous_backend_id=$image_id; current_backend_image=$image_id ;;
            tenant-ui) previous_tenant_ui_id=$image_id ;;
            platform-admin-ui) previous_platform_admin_ui_id=$image_id ;;
        esac
    done
    mv "$rollback_tmp" "$rollback_file"
    echo "Environment backup: $env_backup"
    echo "Rollback compose: $rollback_file"
    ensure_no_active_capture
fi

rollback_previous() {
    rollback_ok=1
    restore_env || rollback_ok=0
    [ -n "$previous_version" ] || rollback_ok=0
    export CARGO_PLATFORM_VERSION=$previous_version
    if [ -z "$rollback_file" ] || ! compose -f "$rollback_file" up -d --no-deps --wait --wait-timeout 180 $services; then
        rollback_ok=0
    else
        for service in $services; do
            container_id=$(compose ps -q "$service") || { rollback_ok=0; continue; }
            [ -n "$container_id" ] || { rollback_ok=0; continue; }
            restored_image=$(docker inspect --format '{{.Image}}' "$container_id") || { rollback_ok=0; continue; }
            [ "$restored_image" = "$(expected_image_id "$service")" ] || rollback_ok=0
        done
        database_integrity_ok || rollback_ok=0
    fi
    if [ "$rollback_ok" -ne 1 ]; then
        preserve_deployment_mutex=1
        return 1
    fi
}

deployment_lock_container=
old_services_stopped=0

release_deployment_lock() {
    if [ -n "$deployment_lock_container" ]; then
        if ! docker rm -f "$deployment_lock_container" >/dev/null 2>&1; then
            preserve_deployment_mutex=1
            return 1
        fi
    fi
    deployment_lock_container=
}

acquire_deployment_lock() {
    deployment_lock_container="cargo-platform-deploy-db-lock"
    docker run --rm -d --name "$deployment_lock_container" --entrypoint python \
        --mount "type=volume,src=$volume_name,dst=/data" \
        "$current_backend_image" -c \
        'import sqlite3,threading; db=sqlite3.connect("/data/cargo-platform.db", timeout=30, isolation_level=None); db.execute("BEGIN EXCLUSIVE"); row=db.execute("SELECT status FROM capture_tasks WHERE status IN (\"collecting\", \"stopping\") LIMIT 1").fetchone(); print("LOCKED:" + (row[0] if row else ""), flush=True); threading.Event().wait()' \
        >/dev/null || return 1
    attempts=0
    while [ "$attempts" -lt 35 ]; do
        lock_line=$(docker logs "$deployment_lock_container" 2>/dev/null | tail -n 1 || true)
        case "$lock_line" in
            LOCKED:*) deployment_lock_status=${lock_line#LOCKED:}; return 0 ;;
        esac
        lock_running=$(docker inspect --format '{{.State.Running}}' "$deployment_lock_container" 2>/dev/null || true)
        [ "$lock_running" = true ] || return 1
        attempts=$((attempts + 1))
        sleep 1
    done
    return 1
}

deployment_interrupted() {
    trap '' HUP INT TERM
    release_deployment_lock || true
    if [ "$old_services_stopped" -eq 1 ]; then
        rollback_previous || echo "error: deployment interrupted and rollback failed; use $rollback_file" >&2
    else
        restart_previous_services || echo "error: deployment interrupted and previous services could not restart" >&2
    fi
    exit 130
}

first_install_interrupted() {
    trap '' HUP INT TERM
    cleanup_incomplete_install || echo "error: first installation was interrupted and incomplete services could not be removed" >&2
    exit 130
}

if ! compose pull $services; then
    restore_env || preserve_deployment_mutex=1
    die "release image pull failed"
fi
for service in $services; do
    target_id=$(docker image inspect --format '{{.Id}}' "$(target_image_ref "$service")") || die "failed to inspect pulled image for $service"
    [ -n "$target_id" ] || die "pulled image has no image ID for $service"
    case "$service" in
        waybill-parser) target_waybill_parser_id=$target_id ;;
        backend) target_backend_id=$target_id ;;
        tenant-ui) target_tenant_ui_id=$target_id ;;
        platform-admin-ui) target_platform_admin_ui_id=$target_id ;;
    esac
done
snapshot_record=
snapshot_name=
if [ "$upgrade" -eq 1 ]; then
    ensure_no_active_capture
    trap deployment_interrupted HUP INT TERM
    if ! acquire_deployment_lock; then
        release_deployment_lock || true
        die "failed to acquire the deployment database lock"
    fi
    case "$(printf '%s' "$deployment_lock_status" | tr -d ' \r\n')" in
        collecting|stopping)
            active_status=$deployment_lock_status
            release_deployment_lock || true
            die "capture task is $active_status; refusing deployment"
            ;;
    esac
    if ! ensure_expected_volume_consumers backend; then
        release_deployment_lock || true
        die "unexpected database volume consumer; refusing deployment before service changes"
    fi
    if ! compose stop tenant-ui platform-admin-ui; then
        release_deployment_lock || true
        restart_previous_services || die "failed to quiesce user entry points and previous services could not restart"
        die "failed to quiesce user entry points"
    fi
    if ! compose stop backend waybill-parser; then
        release_deployment_lock || true
        restart_previous_services || die "failed to stop services and previous services could not restart"
        die "failed to stop previous services"
    fi
    old_services_stopped=1
    if ! ensure_expected_volume_consumers; then
        preserve_deployment_mutex=1
        die "unexpected database volume consumer appeared while stopping services; confirm this deployment ended, stop the unexpected consumer, run 'docker rm -f cargo-platform-deploy-db-lock', restore and verify the old release with the printed files, then run 'docker rm -f cargo-platform-deploy-mutex'"
    fi
    release_deployment_lock || die "failed to release the database lock; confirm this deployment ended, run 'docker rm -f cargo-platform-deploy-db-lock', restore and verify the old release with the printed files, then run 'docker rm -f cargo-platform-deploy-mutex'"
    snapshot_name="$volume_name-$stamp.db"
    if ! snapshot_output=$(
        docker run --rm --entrypoint python \
            --mount "type=volume,src=$volume_name,dst=/data,readonly" \
            --mount "type=bind,src=$snapshot_script,dst=/tool/sqlite_snapshot.py,readonly" \
            --mount "type=bind,src=$backup_dir,dst=/backup" \
            "$current_backend_image" /tool/sqlite_snapshot.py backup \
            /data/cargo-platform.db "/backup/$snapshot_name"
    ); then
        restart_previous_services || die "SQLite snapshot failed and previous services could not restart"
        die "verified SQLite snapshot failed; previous services restarted"
    fi
    if ! printf '%s' "$snapshot_output" | grep -Eq '"integrity_check"[[:space:]]*:[[:space:]]*"ok"'; then
        restart_previous_services || die "SQLite snapshot verification failed and previous services could not restart"
        die "SQLite snapshot did not report a successful integrity check; previous services restarted"
    fi
    snapshot_record="$backup_dir/snapshot-$stamp.json"
    if ! printf '%s\n' "$snapshot_output" >"$snapshot_record" || ! chmod 600 "$snapshot_record"; then
        restart_previous_services || die "snapshot record write failed and previous services could not restart"
        die "failed to persist snapshot record; previous services restarted"
    fi
    set_env_value CARGO_PLATFORM_VERSION "$version" || {
        restore_env || preserve_deployment_mutex=1
        restart_previous_services || die "failed to update .env and previous services could not restart"
        die "failed to update .env for the target release; previous services restarted"
    }
fi
if [ "$upgrade" -eq 0 ]; then
    [ -f "$install_marker" ] || die "first-install marker is missing"
    docker volume create "$volume_name" >/dev/null || die "failed to create $volume_name"
    trap first_install_interrupted HUP INT TERM
fi

if ! compose up -d --wait --wait-timeout 180 $services; then
    if [ "$upgrade" -eq 1 ]; then
        rollback_previous || die "deployment and rollback both failed; restore the database from snapshot record: $snapshot_record"
        die "deployment failed; previous release restored"
    else
        cleanup_incomplete_install || true
        die "deployment failed; first-install state preserved for retry"
    fi
fi

health_ok=1
for service in $services; do
    container_id=$(compose ps -q "$service") || health_ok=0
    [ -n "$container_id" ] || health_ok=0
    actual_target_id=$(docker inspect --format '{{.Image}}' "$container_id") || health_ok=0
    [ "$actual_target_id" = "$(expected_target_image_id "$service")" ] || health_ok=0
done
database_integrity_ok || health_ok=0
backend_ready=$(curl -fsS http://127.0.0.1:8000/api/v1/ready) || health_ok=0
parser_ready=$(curl -fsS http://127.0.0.1:8010/health) || health_ok=0
printf '%s' "$backend_ready" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' || health_ok=0
printf '%s' "$backend_ready" | grep -Eq "\"version\"[[:space:]]*:[[:space:]]*\"$version\"" || health_ok=0
printf '%s' "$parser_ready" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' || health_ok=0
printf '%s' "$parser_ready" | grep -Eq "\"version\"[[:space:]]*:[[:space:]]*\"$version\"" || health_ok=0
curl -fsS -o /dev/null http://127.0.0.1:5173/ >/dev/null || health_ok=0
curl -fsS -o /dev/null http://127.0.0.1:5174/ >/dev/null || health_ok=0

if [ "$health_ok" -ne 1 ]; then
    if [ "$upgrade" -eq 1 ]; then
        rollback_previous || die "readiness failed and rollback failed; restore the database from snapshot record: $snapshot_record"
        die "deployment readiness failed; previous release restored"
    else
        cleanup_incomplete_install || true
        die "deployment readiness failed; first-install state preserved for retry"
    fi
fi

if [ "$upgrade" -eq 0 ]; then
    rm -f "$install_marker" || {
        preserve_deployment_mutex=1
        die "deployment is healthy but the first-install marker could not be finalized; deployment mutex preserved"
    }
fi
trap - HUP INT TERM
release_deployment_mutex || die "deployment succeeded but the deployment mutex could not be removed"
trap - EXIT
echo "Deployed Cargo Platform $version"
if [ -n "$rollback_file" ]; then
    echo "Rollback compose: $rollback_file"
fi
if [ -n "$snapshot_record" ]; then
    echo "Database snapshot: $backup_dir/$snapshot_name"
    echo "Snapshot record: $snapshot_record"
fi
