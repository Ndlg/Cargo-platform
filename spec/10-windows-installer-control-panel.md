# 10 Windows Installer And Local Control Panel

## Decision

The first productized delivery version should be:

```text
Windows installer exe
  -> installs local Cargo Platform package
  -> creates desktop/start-menu shortcut
  -> opens a local control panel exe
  -> control panel manages Docker-backed services
```

Docker Desktop may be required as a dependency in this first version. The user is allowed to install dependencies, but the user must not need to understand Docker, PowerShell scripts, ports, compose files, or container names.

The product goal is not "give the user a repo and scripts". The product goal is "give the user an application they can install, start, stop, configure, and use".

## User Experience Target

The user should receive one installer, for example:

```text
面单整理系统_Setup_20260706.exe
```

After installation, the user should see:

- desktop shortcut: `面单整理系统控制台`
- start menu entry: `面单整理系统控制台`
- optional local data folder: chosen during setup or defaulted by installer

When the user opens the control panel, they should see business-friendly controls:

- current service state
- current data directory
- current ports
- start system
- stop system
- restart system
- open business page
- open administrator page
- view logs
- check/update configuration

The control panel should never make the user copy commands into CMD.

## Product Principle

The control panel is a friendly shell around the existing service architecture.

It may call Docker and docker compose internally, but the UI should only expose product concepts:

- system is running
- system is stopped
- Docker is not installed
- Docker is installed but not running
- port is occupied
- data directory is unavailable
- service health check failed
- parser service is unavailable

It must not expose these as the primary user language:

- `docker compose up`
- container IDs
- compose override internals
- PowerShell execution policies
- volume names
- nginx upstreams

Technical diagnostics can exist behind an advanced/logs view.

## First Version Scope

### Included

1. Windows installer.
2. Local control panel exe.
3. Dependency check for Docker Desktop.
4. Configurable ports.
5. Start/stop/restart services.
6. Health check dashboard.
7. Open business/admin pages.
8. View recent logs.
9. Preserve local data across restart and upgrade.
10. A generated local compose/env configuration owned by the installer.

### Not Included In First Version

1. Full replacement of Docker with native Windows services.
2. Cloud SaaS deployment.
3. Automatic online upgrade system.
4. Multi-user permission control at the installer level.
5. One-click database migration UI.
6. Remote server deployment.

These can be future versions after the local installer is stable.

## Runtime Services

The installer-managed product still runs the current service set:

- backend API
- tenant/business UI
- platform admin UI
- independent waybill parser service
- Redis
- persisted data volume or data directory

Current container names may remain:

- `cargo-platform-backend`
- `cargo-platform-tenant-ui`
- `cargo-platform-admin-ui`
- `cargo-platform-waybill-parser`
- `cargo-platform-redis`

But these names are diagnostics only. Normal users should not need to know them.

## Port Configuration

The control panel should expose editable ports:

| Purpose | Default | User-facing label |
| --- | ---: | --- |
| Business page | `5173` | 业务页面端口 |
| Admin page | `5174` | 管理页面端口 |
| Backend API | `8000` | 后端服务端口 |
| Parser service | `8010` | 识别服务端口 |

Redis does not need a user-facing port by default.

Port rules:

- Ports must be checked before saving.
- If a port is occupied, tell the user which port and what to change.
- If ports are changed, the control panel should regenerate local config and restart affected services.
- Business page and admin page should have "open" buttons after services are healthy.
- CORS and frontend API proxy settings must follow the configured ports.

## Configuration Files

The installed product should own a generated local configuration, not ask users to edit source repo files.

Recommended installed layout:

```text
C:\Program Files\Cargo Platform\
  CargoPlatformControlPanel.exe
  runtime\
    docker-compose.release.yml
    docker-compose.local.yml
    .env
  tools\
    docker-compose-wrapper.ps1

C:\ProgramData\Cargo Platform\
  config\
    app-config.json
  data\
  logs\
```

`docker-compose.release.yml` should describe the shipped images.

`docker-compose.local.yml` should contain machine-specific overrides:

- port mappings
- data mount/volume choice
- environment variables
- local `SECRET_KEY`
- optional image version pin

`app-config.json` should be the control panel source of truth:

```json
{
  "version": 1,
  "ports": {
    "business_ui": 5173,
    "admin_ui": 5174,
    "backend_api": 8000,
    "parser_service": 8010
  },
  "data_mode": "docker_volume",
  "data_volume": "cargo-platform-data",
  "image_version": "0.1.0"
}
```

## Data Preservation

The installer and control panel must preserve:

- database
- uploaded files
- product/SKU/image assets
- rule packs
- collector records
- export/download history

First version should continue using `cargo-platform-data` unless the user explicitly chooses a host data folder.

Rules:

- Stop/restart must not delete data.
- Reinstall/upgrade must not delete data.
- Uninstall should ask whether to keep data.
- Any destructive action must require an explicit warning and confirmation.

## Control Panel States

The control panel should calculate one clear state:

| State | Meaning | User action |
| --- | --- | --- |
| 未安装依赖 | Docker Desktop missing | Show install guide/button |
| Docker未启动 | Docker installed but not running | Ask user to start Docker Desktop |
| 未启动 | Containers are not running | Show "启动系统" |
| 启动中 | Containers starting | Disable duplicate start button |
| 运行中 | All health checks pass | Show open-page buttons |
| 部分异常 | Some services failed health check | Show failed service and logs |
| 端口冲突 | Configured port unavailable | Show port settings |
| 配置错误 | Compose/env invalid | Show repair/reset config |

The user should not need to infer status from logs.

## Health Checks

Minimum health checks:

1. Docker command is available.
2. Docker daemon responds.
3. Compose config is valid.
4. Containers are running.
5. Backend health returns HTTP 200.
6. Parser health returns HTTP 200.
7. Business UI returns HTTP 200.
8. Admin UI returns HTTP 200.

Business-friendly result examples:

- `系统运行中，可以打开业务页面。`
- `识别服务没有启动，面单解析不可用。`
- `业务页面端口 5173 被占用，请换一个端口。`
- `Docker Desktop 没有启动，请先打开 Docker Desktop。`

## Installer Requirements

The installer should:

- install the control panel exe
- install compose/runtime files
- create desktop shortcut
- create start-menu shortcut
- optionally create firewall prompts if remote LAN access is supported later
- write initial config
- detect Docker Desktop
- offer a dependency guide if Docker is absent

First version may not bundle Docker Desktop directly. It may show:

```text
需要安装 Docker Desktop 才能运行本地服务。
```

and provide an "打开下载页面" button or bundled offline installer in a later version.

## Control Panel Implementation Direction

Recommended first implementation:

- Windows desktop app, preferably `.NET` WinForms/WPF or Tauri.
- It runs as a normal user process.
- It calls Docker through controlled wrapper commands.
- It writes local config files.
- It does not run as a long-lived Windows service in the first version.

Reason:

- simplest for Windows users
- easy to package as exe
- easy to expose buttons and status
- avoids rewriting backend/frontend runtime
- keeps existing Docker deployment path

The implementation should avoid coupling the control panel to business code. It is a deployment/runtime manager, not an order-processing module.

## Generated Compose Override

The control panel should generate an override similar to:

```yaml
services:
  backend:
    ports:
      - "127.0.0.1:${BACKEND_PORT}:8000"
    environment:
      CORS_ORIGINS: "http://127.0.0.1:${BUSINESS_UI_PORT},http://localhost:${BUSINESS_UI_PORT},http://127.0.0.1:${ADMIN_UI_PORT},http://localhost:${ADMIN_UI_PORT}"

  waybill-parser:
    ports:
      - "127.0.0.1:${PARSER_PORT}:8010"

  tenant-ui:
    ports:
      - "127.0.0.1:${BUSINESS_UI_PORT}:80"

  platform-admin-ui:
    ports:
      - "127.0.0.1:${ADMIN_UI_PORT}:80"
```

The user should edit ports in the control panel, not in YAML.

## UI Design

Main screen:

```text
面单整理系统控制台

状态：运行中
数据目录：C:\ProgramData\Cargo Platform\data

[启动系统] [停止系统] [重启系统]
[打开业务页面] [打开管理页面]

端口设置
业务页面：5173
管理页面：5174
后端服务：8000
识别服务：8010
[检测端口] [保存并重启]

服务检查
业务页面：正常
后端服务：正常
识别服务：正常
数据存储：正常

[查看日志] [打开数据目录] [导出诊断包]
```

The UI should be quiet and operational, not a developer console.

## Logs And Diagnostics

The control panel should include:

- "查看最近日志"
- "导出诊断包"

Diagnostics package may include:

- current config without secrets
- compose service status
- recent backend logs
- recent parser logs
- recent UI logs if available
- health-check result

This helps support users without asking them to run commands.

## Upgrade Direction

First version upgrade can be manual:

1. User runs a newer installer.
2. Installer detects existing config/data.
3. Installer keeps data.
4. Installer updates runtime files and control panel exe.
5. Control panel restarts services.

Future version may add:

- update check
- one-click image pull
- version rollback
- backup before upgrade

## Acceptance Criteria

The first installer/control-panel version is acceptable when:

1. A non-technical user can install the product without opening a terminal.
2. A desktop shortcut opens the control panel.
3. The control panel detects Docker missing/not running.
4. The user can change business/admin/API/parser ports.
5. The control panel detects occupied ports before applying changes.
6. The user can start, stop, and restart all services from buttons.
7. The user can open the business page from a button after services are healthy.
8. Backend, parser, tenant UI, and admin UI health are shown clearly.
9. Restarting services does not delete data.
10. Reinstalling/upgrading does not delete data.
11. Error messages use business wording instead of raw Docker errors.
12. A support person can export a diagnostics package.

## Implementation Stages

### Stage 1: Runtime Config Foundation

- Introduce local env/compose override template.
- Replace hardcoded release ports with configurable variables.
- Add scripts that can start/stop/check services from generated config.
- Verify existing Docker deployment still works.

### Stage 2: Control Panel MVP

- Build Windows control panel exe.
- Read/write app config.
- Detect Docker.
- Check ports.
- Start/stop/restart services.
- Show health status.
- Open business/admin pages.

### Stage 3: Installer

- Package control panel and runtime files.
- Create shortcuts.
- Initialize config.
- Preserve previous config/data.

### Stage 4: Diagnostics And Polish

- Add log viewer.
- Add diagnostics export.
- Improve failure messages.
- Add first-run guide.

### Stage 5: Acceptance Test On Clean Machine

- Test on a clean Windows machine with Docker Desktop installed.
- Test on a machine without Docker Desktop.
- Test occupied-port handling.
- Test reinstall with existing data.
- Test service restart.

## Risks

### Docker Desktop Availability

Some users may not have Docker Desktop installed or running.

Mitigation:

- detect it early
- show exact reason
- link to install/start instructions

### Port Conflicts

Common ports may already be occupied.

Mitigation:

- preflight check
- editable port fields
- no partial restart until config is valid

### Data Loss Fear

Users may worry that reinstalling deletes their order data.

Mitigation:

- explicit data directory display
- upgrade keeps data by default
- uninstall asks before deleting data

### Hidden Technical Errors

Docker errors can be unreadable.

Mitigation:

- translate common errors
- keep advanced logs available
- diagnostics export for support

## Out Of Scope For This Spec

This spec does not change:

- waybill recognition logic
- rule pack format
- order row review behavior
- product/SKU/image matching
- Excel export format
- collector client protocol

It only productizes how the local system is installed, configured, started, stopped, and diagnosed.
