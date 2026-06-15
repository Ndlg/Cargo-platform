from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "storage" / "server-console"
LOG_DIR = ROOT / "logs" / "server-console"
STATE_FILE = STATE_DIR / "services.json"


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    port: int
    url: str
    cwd: Path
    command: list[str]
    log_name: str


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def system_python() -> str:
    return sys.executable or "python"


def npm_command() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise RuntimeError("未找到 npm。请先安装 Node.js，或确认 npm 已加入 PATH。")
    return npm


def backend_service() -> Service:
    python = str(venv_python()) if venv_python().exists() else system_python()
    return Service(
        key="backend",
        name="后端 API",
        port=8000,
        url="http://127.0.0.1:8000/docs",
        cwd=ROOT,
        command=[
            python,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "backend",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--reload",
        ],
        log_name="backend.log",
    )


def tenant_frontend_service() -> Service:
    return Service(
        key="tenant_frontend",
        name="客户 UI",
        port=5173,
        url="http://127.0.0.1:5173",
        cwd=ROOT / "frontend",
        command=[npm_command(), "run", "dev"],
        log_name="tenant-frontend.log",
    )


def server_admin_service() -> Service:
    return Service(
        key="server_admin_frontend",
        name="平台后台 UI",
        port=5174,
        url="http://127.0.0.1:5174/admin",
        cwd=ROOT / "frontend",
        command=[npm_command(), "run", "dev:server-admin"],
        log_name="server-admin-frontend.log",
    )


def collector_service(token: str) -> Service:
    python = str(venv_python()) if venv_python().exists() else system_python()
    return Service(
        key="collector",
        name="本机采集器",
        port=0,
        url="",
        cwd=ROOT,
        command=[
            python,
            "collector-client/client.py",
            "--base-url",
            "http://127.0.0.1:8000/api/v1",
            "--token",
            token,
            "--loop",
        ],
        log_name="collector.log",
    )


def core_services(include_admin: bool = False) -> list[Service]:
    services = [backend_service(), tenant_frontend_service()]
    if include_admin:
        services.append(server_admin_service())
    return services


def read_state() -> dict[str, dict[str, object]]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_state(state: dict[str, dict[str, object]]) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def port_open(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_checked(command: list[str], cwd: Path) -> None:
    print(f"执行：{' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), check=True)


def ensure_backend_dependencies() -> None:
    if not venv_python().exists():
        print("首次启动：正在创建后端 Python 虚拟环境...")
        run_checked([system_python(), "-m", "venv", ".venv"], ROOT)

    marker = STATE_DIR / "backend-deps.ok"
    requirements = ROOT / "backend" / "requirements.txt"
    needs_install = not marker.exists() or marker.stat().st_mtime < requirements.stat().st_mtime
    if needs_install:
        print("正在安装/更新后端依赖...")
        run_checked([str(venv_python()), "-m", "pip", "install", "-r", "backend/requirements.txt"], ROOT)
        marker.write_text(now_text(), encoding="utf-8")


def ensure_frontend_dependencies() -> None:
    node_modules = ROOT / "frontend" / "node_modules"
    if node_modules.exists():
        return
    print("首次启动：正在安装前端依赖...")
    run_checked([npm_command(), "install"], ROOT / "frontend")


def install_dependencies() -> None:
    ensure_dirs()
    ensure_backend_dependencies()
    ensure_frontend_dependencies()
    print("依赖检查完成。")


def start_service(service: Service) -> None:
    ensure_dirs()
    state = read_state()

    existing = state.get(service.key, {})
    existing_pid = int(existing.get("pid") or 0)
    if pid_alive(existing_pid):
        print(f"{service.name} 已由控制台启动，PID={existing_pid}")
        return
    if service.port and port_open(service.port):
        print(f"{service.name} 端口 {service.port} 已在运行，控制台不会重复启动。")
        return

    log_path = LOG_DIR / service.log_name
    log_file = log_path.open("a", encoding="utf-8", errors="replace")
    log_file.write(f"\n\n[{now_text()}] start {' '.join(service.command)}\n")
    log_file.flush()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env.setdefault("BROWSER", "none")

    process = subprocess.Popen(
        service.command,
        cwd=str(service.cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    log_file.close()

    state[service.key] = {
        "pid": process.pid,
        "name": service.name,
        "port": service.port,
        "url": service.url,
        "log": str(log_path),
        "command": service.command,
        "started_at": now_text(),
    }
    write_state(state)
    print(f"{service.name} 已启动，PID={process.pid}，日志：{log_path}")


def start_services(include_admin: bool = False) -> None:
    install_dependencies()
    for service in core_services(include_admin=include_admin):
        start_service(service)
    print_urls()


def start_collector(token: str) -> None:
    if not token.strip():
        print("collector_token 不能为空。")
        return
    start_service(collector_service(token.strip()))


def stop_service(key: str) -> None:
    state = read_state()
    entry = state.get(key)
    if not entry:
        return
    pid = int(entry.get("pid") or 0)
    name = str(entry.get("name") or key)
    if pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL)
        else:
            subprocess.run(["kill", str(pid)], check=False)
        print(f"已停止 {name}，PID={pid}")
    state.pop(key, None)
    write_state(state)


def stop_all() -> None:
    for key in ["collector", "server_admin_frontend", "tenant_frontend", "backend"]:
        stop_service(key)
    print("停止命令已执行。")


def print_urls() -> None:
    print("\n常用地址：")
    print("  客户业务页：   http://127.0.0.1:5173")
    print("  客户管理页：   http://127.0.0.1:5173/admin")
    print("  平台后台：     http://127.0.0.1:5174/admin")
    print("  后端接口文档： http://127.0.0.1:8000/docs")


def show_status() -> None:
    state = read_state()
    rows = [
        ("后端 API", "backend", 8000, "http://127.0.0.1:8000/docs"),
        ("客户 UI", "tenant_frontend", 5173, "http://127.0.0.1:5173"),
        ("平台后台 UI", "server_admin_frontend", 5174, "http://127.0.0.1:5174/admin"),
        ("本机采集器", "collector", 0, ""),
    ]

    print("\n服务状态：")
    for name, key, port, url in rows:
        entry = state.get(key, {})
        pid = int(entry.get("pid") or 0)
        pid_status = "PID在线" if pid_alive(pid) else "PID未知"
        port_status = "端口可访问" if port and port_open(port) else ("无端口" if not port else "端口未通")
        log_path = entry.get("log") or ""
        print(f"  {name:<12} {pid_status:<8} {port_status:<8} PID={pid or '-'}")
        if url:
            print(f"    URL: {url}")
        if log_path:
            print(f"    LOG: {log_path}")
    print_urls()


def open_url(url: str) -> None:
    print(f"打开：{url}")
    webbrowser.open(url)


def menu() -> None:
    while True:
        print("\n=== Cargo Platform服务器控制台 ===")
        print("1. 一键启动客户系统（后端 + 客户 UI）")
        print("2. 启动全部（后端 + 客户 UI + 平台后台）")
        print("3. 查看服务状态")
        print("4. 打开客户业务页")
        print("5. 打开客户管理页")
        print("6. 打开平台后台")
        print("7. 启动本机采集器")
        print("8. 安装/更新依赖")
        print("9. 停止全部服务")
        print("0. 退出")
        choice = input("请选择：").strip()

        try:
            if choice == "1":
                start_services(include_admin=False)
            elif choice == "2":
                start_services(include_admin=True)
            elif choice == "3":
                show_status()
            elif choice == "4":
                open_url("http://127.0.0.1:5173")
            elif choice == "5":
                open_url("http://127.0.0.1:5173/admin")
            elif choice == "6":
                open_url("http://127.0.0.1:5174/admin")
            elif choice == "7":
                token = input("请输入客户管理页生成的 collector_token：").strip()
                start_collector(token)
            elif choice == "8":
                install_dependencies()
            elif choice == "9":
                stop_all()
            elif choice == "0":
                return
            else:
                print("无效选择。")
        except Exception as exc:
            print(f"操作失败：{exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Order system local server console.")
    parser.add_argument("--start", action="store_true", help="Start backend and tenant UI.")
    parser.add_argument("--start-all", action="store_true", help="Start backend, tenant UI and platform admin UI.")
    parser.add_argument("--stop", action="store_true", help="Stop services started by this console.")
    parser.add_argument("--status", action="store_true", help="Show service status.")
    parser.add_argument("--install", action="store_true", help="Install or update dependencies.")
    parser.add_argument("--collector-token", help="Start local collector with a collector token.")
    args = parser.parse_args()

    try:
        if args.install:
            install_dependencies()
        elif args.start:
            start_services(include_admin=False)
        elif args.start_all:
            start_services(include_admin=True)
        elif args.stop:
            stop_all()
        elif args.status:
            show_status()
        elif args.collector_token:
            start_collector(args.collector_token)
        else:
            menu()
    except KeyboardInterrupt:
        print("\n已取消。")
    except Exception as exc:
        print(f"操作失败：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
