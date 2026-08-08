# 服务端 Docker 部署

当前发行版：`1.0.2`

服务端支持 Intel/AMD（`linux/amd64`）和 Apple Silicon（`linux/arm64`）。Mac 只承载服务端；业务机采集器仍安装在 Windows 电脑上。

## Mac 首次安装

准备 Docker Desktop、Git，并确认 `5173`、`5174`、`8000`、`8010` 端口没有被占用。

```sh
git clone --branch v1.0.2 --depth 1 https://github.com/Ndlg/Cargo-platform.git cargo-platform
cd cargo-platform
./scripts/deploy_server.sh
```

部署脚本会自动：

- 创建权限为 `0600` 的 `.env`，生成三项随机密钥；
- 创建外部数据卷 `cargo-platform-data`；
- 拉取同为 `1.0.2` 的四个服务镜像；
- 等待后端、识别服务和两个页面全部就绪。

首次初始化所需 Token：

```sh
awk -F= '$1 == "INITIAL_SETUP_TOKEN" { print $2 }' .env
```

打开业务页面后，首次访问会要求输入这个 Token，并设置管理员用户名和密码；初始化完成后 Token 不再用于日常登录。

业务页面：`http://<Mac 的局域网 IP>:5173/`

平台管理页面默认只监听 Mac 本机：`http://127.0.0.1:5174/admin`。从其他电脑访问时使用 SSH 隧道：

```sh
ssh -L 5174:127.0.0.1:5174 <Mac用户名>@<Mac的局域网IP>
```

随后在当前电脑打开 `http://127.0.0.1:5174/admin`。

## 查看状态与日志

```sh
docker compose -f docker-compose.release.yml ps
docker compose -f docker-compose.release.yml logs --tail=200
```

停止和恢复服务不会删除数据：

```sh
docker compose -f docker-compose.release.yml stop
docker compose -f docker-compose.release.yml start
```

不要执行 `docker compose down -v`，也不要删除 `cargo-platform-data`。

## 升级

先在业务页面结束当前采集轮次，再切换到新标签并运行同一命令：

```sh
git fetch --tags
git checkout v1.0.2
./scripts/deploy_server.sh
```

脚本会先安全拉取一个后端镜像作为全局部署锁载体；发现 `collecting` 或 `stopping` 状态时，会在停止服务、快照或重建前拒绝升级。正常升级会：

1. 保留现有 `.env` 密钥，仅更新发行版本；
2. 在项目同级的 `cargo-platform-deploy-backups` 中生成校验通过的 SQLite 快照；
3. 记录原四个镜像和回退 Compose；
4. 新版本未通过就绪检查时自动恢复旧 `.env` 和旧镜像。

自定义备份目录：

```sh
./scripts/deploy_server.sh --backup-dir "$HOME/cargo-platform-backups"
```

若终端被强制结束，部署锁会故意保留以阻止第二次部署。升级脚本会在停止服务前打印 `.env` 备份和回退 Compose 的准确路径；中断时不要直接清锁重试。先确认原部署进程已结束，在数据库锁仍保持时依次停止 `tenant-ui platform-admin-ui`、`backend waybill-parser` 和其他占用 `cargo-platform-data` 的容器，再执行 `docker rm -f cargo-platform-deploy-db-lock`；随后按下节恢复并验证旧四服务和数据库，最后才执行 `docker rm -f cargo-platform-deploy-mutex`。首次安装中断则保留数据卷和安装标记；若仍有残留容器，先确认没有活动采集，再用 `docker compose -f docker-compose.release.yml rm -f -s waybill-parser backend tenant-ui platform-admin-ui` 只清理四个服务容器（不删除数据卷和安装标记），随后仅重试同一版本。

## 手工回退镜像

仅当自动回退未完成时使用脚本输出的回退文件：

```sh
cp <备份目录>/.env-<时间戳> .env
docker compose \
  -f docker-compose.release.yml \
  -f <备份目录>/docker-compose.rollback-<时间戳>.yml \
  up -d --no-deps --wait --wait-timeout 180 \
  waybill-parser backend tenant-ui platform-admin-ui
```

数据库快照不会在镜像回退时自动覆盖当前数据库。只有确认需要恢复数据时，才执行下面的命令；把文件名、备份目录和 SHA-256 换成部署脚本输出及 `snapshot-<时间戳>.json` 中的值：

```sh
set -eu
rollback_file=<备份目录>/docker-compose.rollback-<时间戳>.yml
test -f "$rollback_file"
backend_image="$(docker inspect --format '{{.Image}}' cargo-platform-backend)"
deployment_lock_exists=0
database_lock_exists=0
docker inspect cargo-platform-deploy-mutex >/dev/null 2>&1 && deployment_lock_exists=1
docker inspect cargo-platform-deploy-db-lock >/dev/null 2>&1 && database_lock_exists=1
if [ "$deployment_lock_exists" -eq 1 ] || [ "$database_lock_exists" -eq 1 ]; then
  test "${CONFIRM_REUSE_STALE_LOCK:-}" = RESTORE || {
    echo "部署锁或数据库锁已存在；确认没有部署进程后，以 CONFIRM_REUSE_STALE_LOCK=RESTORE 重新执行恢复" >&2
    exit 1
  }
fi
if [ "$deployment_lock_exists" -eq 0 ]; then
  docker run --rm -d --name cargo-platform-deploy-mutex --entrypoint python \
    "$backend_image" -c 'import threading; threading.Event().wait()' >/dev/null
fi
trap 'echo "恢复未完成，服务和部署锁保持当前状态；排查后继续恢复，不要启动或部署" >&2' EXIT
docker compose -f docker-compose.release.yml -f "$rollback_file" stop tenant-ui platform-admin-ui
docker compose -f docker-compose.release.yml -f "$rollback_file" stop backend waybill-parser
running="$(docker ps -q --no-trunc --filter volume=cargo-platform-data)" || {
  echo "无法确认 cargo-platform-data 是否仍被使用，数据库锁保持不变" >&2
  exit 1
}
for container in $running; do
  name="$(docker inspect --format '{{.Name}}' "$container")" || exit 1
  test "$name" = /cargo-platform-deploy-db-lock || {
    echo "意外容器仍在使用 cargo-platform-data：${name#/}；数据库锁保持不变" >&2
    exit 1
  }
done
if [ "$database_lock_exists" -eq 1 ]; then
  docker rm -f cargo-platform-deploy-db-lock
fi
running="$(docker ps -q --filter volume=cargo-platform-data)" || {
  echo "无法确认 cargo-platform-data 是否仍被使用，拒绝恢复" >&2
  exit 1
}
test -z "$running" || {
  echo "仍有容器正在使用 cargo-platform-data，拒绝恢复" >&2
  exit 1
}
docker run --rm --entrypoint python \
  --mount type=volume,src=cargo-platform-data,dst=/data \
  --mount "type=bind,src=<备份目录>,dst=/backup,readonly" \
  --mount "type=bind,src=$(pwd)/scripts/sqlite_snapshot.py,dst=/tool/sqlite_snapshot.py,readonly" \
  "$backend_image" /tool/sqlite_snapshot.py restore \
  /backup/<快照文件.db> /data/cargo-platform.db \
  --expected-sha256 <快照记录中的sha256> \
  --confirm RESTORE_STOPPED_DATABASE
docker compose -f docker-compose.release.yml -f "$rollback_file" \
  up -d --no-deps --wait --wait-timeout 180 \
  waybill-parser backend tenant-ui platform-admin-ui
for service in waybill-parser backend tenant-ui platform-admin-ui; do
  container="$(docker compose -f docker-compose.release.yml -f "$rollback_file" ps -q "$service")"
  test -n "$container"
  expected="$(awk -v target="$service" '
    $1 == target ":" { matched=1; next }
    matched && $1 == "image:" { print $2; exit }
    matched && $1 ~ /^[A-Za-z0-9_-]+:$/ { exit }
  ' "$rollback_file")"
  actual="$(docker inspect --format '{{.Image}}' "$container")"
  test -n "$expected" && test "$actual" = "$expected" || {
    echo "$service 未恢复到记录的旧镜像，部署锁保持不变" >&2
    exit 1
  }
done
docker exec cargo-platform-backend python -c 'import sqlite3; db=sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True); assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)'
curl -fsS http://127.0.0.1:8000/api/v1/ready >/dev/null
curl -fsS http://127.0.0.1:8010/health >/dev/null
curl -fsS -o /dev/null http://127.0.0.1:5173/
curl -fsS -o /dev/null http://127.0.0.1:5174/
docker rm -f cargo-platform-deploy-mutex
trap - EXIT
```

恢复命令会再次校验 SHA-256 和 SQLite 完整性；校验失败不会替换数据库。手工恢复期间只允许一名操作者，禁止执行其他 `start` 或部署命令；若复用强停后留下的锁，必须先确认原部署进程已经结束。

## 使用发行压缩包

GitHub Release 同时提供：

- `cargo-platform-server-1.0.2.zip`
- `cargo-platform-server-1.0.2.tar.gz`
- `server-manifest.json`
- `SHA256SUMS-server.txt`

校验后解压，进入目录执行 `./scripts/deploy_server.sh` 即可。压缩包不包含 `.env`、数据库、日志或任何运行时 Token。
