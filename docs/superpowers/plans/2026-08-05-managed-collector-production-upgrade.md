# Managed Collector Production Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a one-code Windows managed collector, upgrade the 5173 production stack to the validated 6173 protocol-v2 baseline, upgrade all three business-machine collectors, and remove obsolete collector launch paths without losing print events.

**Architecture:** Keep the existing protocol-v2 capture engine and add only the missing lifecycle shell. The backend issues single-use enrollment codes and never revokes stale collectors automatically; one PyInstaller EXE installs itself machine-wide, protects the device token with DPAPI, and registers a SYSTEM startup task. Production is upgraded server-first for backward compatibility, then collectors are upgraded one at a time with machine-local rollback backups.

**Tech Stack:** FastAPI, SQLAlchemy, Vue 3/TypeScript/Element Plus, Python 3.12 stdlib, PyInstaller, Windows DPAPI via `ctypes`, Windows Task Scheduler via `schtasks.exe`, Docker Compose, SQLite online backup.

## Global Constraints

- Start from commit `1826ddc525e397668dd54cde965e5de845d8c2b7`, the validated 6173 rc.2 baseline.
- Preserve external Docker volume `cargo-platform-data`; never delete or recreate it.
- Upgrade the server before collectors so the three existing v1 collectors remain accepted during transition.
- Preserve every print event, including identical-content reprints; advance local cursors only after server acknowledgement.
- Do not stop or modify CNPrintClient, CloudPrintClient, browsers, or unrelated business processes.
- Back up each machine's EXE, config, state, logs, scheduled-task XML, startup entries, and hashes before replacement.
- Use npm/package-lock for frontend and `scripts/backend_test.ps1` for backend tests.
- No MSI, Windows Service wrapper, third-party updater, PowerShell startup script, or new runtime dependency.
- All code changes follow red-green-refactor; each deployment checkpoint is independently reversible.

---

### Task 1: Stop stale collectors from losing credentials

**Files:**
- Modify: `backend/app/api/routes/collector_runtime.py:75,166-221,2293-2308`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Consumes: existing `public_collector(collector)` stale-heartbeat projection.
- Produces: `collector_status()` that marks stale collectors offline without mutating `is_enabled`, `token_hash`, or `is_deleted`.

- [ ] **Step 1: Write the failing regression test**

Add an API regression test that registers a collector, moves its heartbeat more than
24 hours into the past, calls `GET /api/v1/collector-control/status`, and reloads the
database row. Assert that the response projects `online_status=offline` while
`is_enabled`, `is_deleted`, and `token_hash` remain unchanged.

- [ ] **Step 2: Run the test and verify it fails because status cleanup revokes the collector**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "status_does_not_revoke" -v`

Expected: FAIL with the collector deleted or absent.

- [ ] **Step 3: Remove `COLLECTOR_CLEANUP_TIMEOUT`, cleanup helpers, and the status-page cleanup call**

Keep `collector_heartbeat_is_stale()` and `public_collector()` as read-only projection logic.

- [ ] **Step 4: Run the targeted collector tests**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/api/routes/collector_runtime.py backend/tests/test_collector_client_runtime.py
git commit -m "fix: keep offline collector credentials recoverable"
```

### Task 2: Add single-use connection codes and repair enrollment

**Files:**
- Create: `backend/app/services/collector_enrollment.py`
- Modify: `backend/app/api/routes/collector_runtime.py:425-440,2038-2085,2181-2290,2792-2842`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Produces: `build_connection_code(base_url: str, token: str) -> str`.
- Produces: `POST /api/v1/collector-runtime/enroll` returning `{collector, collector_token}` after rotating the one-time token.
- Produces: `POST /api/v1/collector-control/{collector_id}/repair-code` returning `{collector, connection_code}`.
- Changes: `POST /collector-control/register` returns `{collector, connection_code}` and does not expose a reusable device token.

- [ ] **Step 1: Write failing tests for code shape, one-time exchange, expiry, and repair**

Add API tests using `TestClient`: register with
`public_base_url=http://10.0.0.5:5173`, assert the only credential returned is a
`CP1.` connection code, enroll once, verify the returned device token can heartbeat,
verify a second enrollment is rejected, and verify an expired enrollment is rejected
without changing the stored device credential.

- [ ] **Step 2: Run the tests and verify missing interfaces fail**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "connection_code or enrollment" -v`

Expected: collection errors for missing service/route or failing assertions against the legacy token response.

- [ ] **Step 3: Implement minimal enrollment service and route changes**

Connection-code payload:

```json
{"v":1,"base_url":"http://10.0.0.5:5173","token":"one-time-token"}
```

Encode as UTF-8 JSON with URL-safe base64 and prefix `CP1.`. Record `runtime_status=enrollment_pending` and `enrollment_expires_at` in existing `status_payload`; do not add a table or migration. Enrollment rotates `token_hash` before returning the device token.

- [ ] **Step 4: Run targeted and full collector route tests**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/services/collector_enrollment.py backend/app/api/routes/collector_runtime.py backend/tests/test_collector_client_runtime.py
git commit -m "feat: add one-time collector enrollment"
```

### Task 3: Add protected Windows configuration and machine-wide migration

**Files:**
- Create: `collector-client/windows_host.py`
- Modify: `collector-client/client.py:80-105,211-266,1547-1637`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Produces: `decode_connection_code(code: str) -> tuple[str, str]`.
- Produces: `protect_secret(value: str) -> str` and `unprotect_secret(value: str) -> str` using DPAPI machine scope on Windows.
- Produces: `machine_paths() -> WindowsCollectorPaths` for Program Files/ProgramData locations.
- Produces: `migrate_legacy_home(paths) -> MigrationResult` that copies, never deletes, legacy state.

- [ ] **Step 1: Write failing tests for decoding, malformed codes, protected round-trip, and migration preservation**

```python
def test_connection_code_decoder_rejects_wrong_prefix() -> None:
    with pytest.raises(ValueError, match="连接码"):
        windows_host.decode_connection_code("TOKEN abc")

def test_legacy_state_migration_never_overwrites_newer_machine_state(tmp_path) -> None:
    legacy = write_state(tmp_path / "legacy", rowid=41)
    machine = write_state(tmp_path / "machine", rowid=55)
    result = windows_host.migrate_legacy_home(paths_for(legacy, machine))
    assert read_rowid(machine) == 55
    assert result.backup_path.exists()
```

On Windows, add a real DPAPI round-trip test. On non-Windows, the function must reject machine protection rather than silently storing plaintext.

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "connection_code_decoder or dpapi or legacy_state_migration" -v`

Expected: import failure for `windows_host`.

- [ ] **Step 3: Implement the Windows-only module with stdlib**

Use `ctypes.windll.crypt32.CryptProtectData`/`CryptUnprotectData`, `CRYPTPROTECT_LOCAL_MACHINE`, `shutil.copy2`, and atomic temp-file replacement. Do not add pywin32.

- [ ] **Step 4: Make `CollectorConfig.load/save` understand `dpapi:` tokens while preserving legacy plaintext reads**

New machine-wide writes must use `dpapi:`. Legacy plaintext is accepted only for migration and rewritten encrypted during install.

- [ ] **Step 5: Run targeted collector tests and commit**

```powershell
scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -q
git add collector-client/windows_host.py collector-client/client.py backend/tests/test_collector_client_runtime.py
git commit -m "feat: protect managed collector state"
```

### Task 4: Add install, repair, rollback, and scheduled-task hosting

**Files:**
- Modify: `collector-client/windows_host.py`
- Modify: `collector-client/client.py:1547-1637`
- Modify: `scripts/build_collector_release.ps1:94-166`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Produces: `install_collector(exe_path, connection_code=None, migrate_existing=False, runner=subprocess.run) -> InstallResult`.
- Produces: CLI flags `--install-code-file`, `--install-existing`, `--uninstall`, `--managed-run`, and `--quiet`.
- Produces: task name `CargoPlatformCollector` with SYSTEM boot trigger, `RestartOnFailure=PT1M`, and `MultipleInstancesPolicy=IgnoreNew`.

- [ ] **Step 1: Write failing tests for task XML, backup-before-replace, rollback, and state preservation**

```python
def test_managed_task_restarts_and_forbids_parallel_instances() -> None:
    xml = windows_host.managed_task_xml(r"C:\Program Files\CargoPlatformCollector\collector.exe")
    assert "<RestartOnFailure>" in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert "S-1-5-18" in xml

def test_failed_install_restores_previous_exe_and_state(tmp_path) -> None:
    result = install_with_failing_health_check(tmp_path)
    assert result.rolled_back is True
    assert old_exe_hash(tmp_path) == EXPECTED_OLD_HASH
    assert state_rowid(tmp_path) == 88
```

- [ ] **Step 2: Run tests and confirm missing behavior fails**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "managed_task or failed_install" -v`

- [ ] **Step 3: Implement minimal installer**

The installer stops only the named managed task/known collector executable, backs up files and task XML, installs to Program Files, writes ProgramData config, registers and starts the task, then calls the existing `--check`. Any failure restores backups.

- [ ] **Step 4: Add default double-click setup UI**

Use stdlib `tkinter`: one connection-code input, install button, progress text, and final success/failure. `--quiet` suppresses UI for SSH deployment.

- [ ] **Step 5: Build and execute the real Windows artifact check**

Run: `$gitSha = (git rev-parse HEAD).Trim(); scripts/build_collector_release.ps1 -Version 1.0.0-rc.3 -GitSha $gitSha`

Expected: valid MZ EXE, manifest SHA match, and `--check` smoke exit 0.

- [ ] **Step 6: Commit**

```powershell
git add collector-client/windows_host.py collector-client/client.py scripts/build_collector_release.ps1 backend/tests/test_collector_client_runtime.py
git commit -m "feat: install and supervise Windows collector"
```

### Task 5: Replace token/command UI with installation and repair flow

**Files:**
- Modify: `frontend/src/services/api.ts:126-139,986-999,1226-1238`
- Modify: `frontend/src/views/workbench/CollectorConnectionsView.vue:1-210,213-405`

**Interfaces:**
- Consumes: register/repair responses containing `connection_code`.
- Produces: management actions `添加业务机`, `下载安装器`, `复制连接码`, and `修复连接`; no raw token or launch command.

- [ ] **Step 1: Change frontend types and API calls to the new responses**

`CollectorRegistrationResponse` contains `collector` and `connection_code`. Add `repairCollectorConnection(id)`.

- [ ] **Step 2: Replace token dialog with a three-step installation dialog**

The dialog shows only the installer download button, one read-only connection-code field, and copy button. Remove `collectorLaunchCommand`, `generatedToken`, token textareas, and command-copy actions.

- [ ] **Step 3: Add repair action for auth/cleaned/offline collectors**

Repair must explicitly warn that issuing a new code invalidates the previous credential. Normal network-offline devices do not need repair.

- [ ] **Step 4: Run frontend typecheck**

Run: `scripts/frontend_typecheck.ps1`

Expected: PASS with no TypeScript errors.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/services/api.ts frontend/src/views/workbench/CollectorConnectionsView.vue
git commit -m "feat: simplify collector installation workflow"
```

### Task 6: Full verification and 6173 release candidate deployment

**Files:**
- Update: `docs/operations/managed-collector-upgrade.md`
- Generated, not committed: `collector-client/dist/*`, Docker images, validation evidence under `outputs/`

**Interfaces:**
- Produces release `1.0.0-rc.3` and a rollback manifest.

- [ ] **Step 1: Run full gates**

```powershell
scripts/backend_test.ps1
scripts/frontend_typecheck.ps1
```

Expected: 377 baseline tests plus new tests all PASS; frontend typecheck PASS.

- [ ] **Step 2: Build release artifacts and four images**

Run: `$gitSha = (git rev-parse HEAD).Trim(); scripts/release_images.ps1 -Version 1.0.0-rc.3 -GitSha $gitSha`

- [ ] **Step 3: Deploy only validation ports 6173/6174 and copied data**

Verify backend/parser readiness, UI HTTP 200, container image IDs, restart counts, collector manifest SHA, and database hash before/after.

- [ ] **Step 4: Exercise failure matrix**

Kill collector process, end/start managed task, block server connection, restore connection, run two instances, corrupt the staged update, and verify cursor/event invariants. Do not touch a business machine in this task.

- [ ] **Step 5: Record evidence and commit**

```powershell
git add docs/operations/managed-collector-upgrade.md docs/validation/2026-08-05-managed-collector-rc3.md
git commit -m "docs: record managed collector validation"
```

### Task 7: Upgrade 5173 production server with rollback

**Files/Artifacts:**
- Runtime: `cargo-platform-data`, four production containers
- Create outside repo: timestamped deployment manifest, SQLite snapshot, rollback compose file

- [ ] **Step 1: Confirm no active capture task and all three old collectors are online**

Abort production cutover if a capture task is collecting.

- [ ] **Step 2: Capture pre-upgrade evidence**

Record container IDs/images/restart counts, database SHA-256 and `PRAGMA integrity_check`, collector IDs/versions/status, and three recent completed-task coverage totals without reading raw business payloads.

- [ ] **Step 3: Push immutable `1.0.0-rc.3` images and commit branch**

Use `scripts/release_images.ps1 -Version 1.0.0-rc.3 -Push` only from a clean committed worktree.

- [ ] **Step 4: Deploy server-first with existing guarded script**

Run: `$backupDir = Join-Path (Split-Path (git rev-parse --show-toplevel) -Parent) ("cargo-platform-deploy-backups\managed-collector-" + (Get-Date -Format "yyyyMMdd-HHmmss")); scripts/deploy_business_containers.ps1 -BackupDirectory $backupDir`.

- [ ] **Step 5: Verify old collectors remain online before touching machines**

Check 5173/5174 HTTP 200, backend/parser version, image IDs, DB integrity, no active capture, and all three legacy heartbeats.

### Task 8: Upgrade and clean the three business machines

**Artifacts:**
- Local release: verified collector EXE and manifest
- Remote backup: set `$stamp = Get-Date -Format "yyyyMMdd-HHmmss"` and use `C:\ProgramData\CargoPlatformCollector\rollback\$stamp` on each machine.

- [ ] **Step 1: Read-only inventory `biz-right`, `biz-middle`, and `biz-left`**

Record Windows account, running collector executable path/hash/command line, config/state/log hashes, collector-related scheduled tasks, Run/RunOnce values, Startup files, and current adapter paths. Never display token contents.

- [ ] **Step 2: Upgrade `biz-right`**

Copy verified EXE/manifest, run `--install-existing --quiet`, confirm one managed task/process, both adapters, new version heartbeat, unchanged state hash/semantic cursor, and zero pending rows after acknowledgement. On failure restore only this machine.

- [ ] **Step 3: Repeat for `biz-middle`**

Use the same pre-check, backup, install, health, and rollback gates.

- [ ] **Step 4: Repeat for `biz-left`**

Use the same pre-check, backup, install, health, and rollback gates.

- [ ] **Step 5: Clean obsolete collector launch paths**

After all three are healthy, export and remove only collector-related old task/startup entries. Move old executables/scripts/config copies into the rollback directory; do not delete the new ProgramData state.

- [ ] **Step 6: Run production closure checks**

Confirm three rc.3 heartbeats, no active old collector process/task, no PowerShell popup path, database integrity, and `collected prints = normal export coverage + exception coverage` on the next completed capture round.

- [ ] **Step 7: Push branch and deliver rollback commands**

Push `codex/managed-collector-production-upgrade`, record commit/image/EXE hashes, server rollback compose, database snapshot, and per-machine restore commands.
