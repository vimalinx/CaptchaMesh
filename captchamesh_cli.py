#!/usr/bin/env python3
"""Installable command-line entry point for the CaptchaMesh local bridge."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import stat
import sys
from pathlib import Path

import requests
from hypercorn.asyncio import serve
from hypercorn.config import Config

from diagnostic_log import DiagnosticLog
from local_bridge import PairingManager, make_local_bridge
from pair_device import _read_secret
from skill_manager import (
    SkillInstallError,
    format_status,
    install_skill,
    run_bundled_inspector,
    skill_status,
)


def default_state_file() -> Path:
    configured = os.environ.get("CAPTCHAMESH_STATE_FILE")
    if configured:
        return Path(configured).expanduser()
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    global_state = config_home / "captchamesh" / "relay-pairing.json"
    if global_state.is_file():
        return global_state
    project_state = Path.cwd() / ".secrets" / "relay-pairing.json"
    if project_state.is_file():
        return project_state
    return global_state


def ensure_private_key(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if path.exists():
        value = read_private_text(path)
        if not value:
            raise RuntimeError(f"本机 API Key 文件为空：{path}")
        return value
    value = "local-" + secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _validate_private_descriptor(descriptor, path)
        _write_all(descriptor, (value + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    return value


def _validate_private_descriptor(descriptor: int, path: Path) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError(f"受限状态文件必须是单链接普通文件：{path}")


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written < 1:
            raise OSError("写入受限状态文件失败")
        remaining = remaining[written:]


def read_private_text(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _validate_private_descriptor(descriptor, path)
        if os.fstat(descriptor).st_size > 65536:
            raise RuntimeError(f"受限状态文件异常过大：{path}")
        os.fchmod(descriptor, 0o600)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 8192):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8").strip()
    finally:
        os.close(descriptor)


def write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _validate_private_descriptor(descriptor, path)
        os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, (value + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def bridge_is_running(host: str, port: int) -> bool:
    host_for_url = "127.0.0.1" if host in {"::1", "localhost"} else host
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(f"http://{host_for_url}:{port}/healthz", timeout=0.5)
        value = response.json()
    except (requests.RequestException, ValueError):
        return False
    return response.status_code == 200 and value.get("service") == "captchamesh-local-bridge"


def require_available_port(host: str, port: int) -> None:
    if not 1 <= port <= 65535:
        raise SystemExit("端口必须在 1 到 65535 之间")
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise SystemExit(
            f"本机端口 {port} 已被占用；请停止旧实例，或同时为 start 和 Agent 指定其他端口"
        ) from exc
    finally:
        probe.close()


def print_setup_url(
    url: str, *, force: bool = False, private_file: Path | None = None
) -> None:
    """Print a capability URL only to an interactive terminal by default."""
    if private_file is not None:
        write_private_text(private_file, url)
    if force or sys.stdout.isatty():
        print(f"配对与状态：{url}", flush=True)
        return
    if private_file is not None:
        print(f"配对链接已写入受限文件：{private_file}", flush=True)
    else:
        print("配对链接已从非交互输出中隐藏", flush=True)


async def run_start(args: argparse.Namespace) -> None:
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("本地兼容桥只允许监听 loopback 地址")
    state_file = args.state_file.expanduser()
    state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    local_key_file = state_file.parent / "local-api.key"
    setup_token_file = state_file.parent / f"setup-token-{args.port}"
    setup_url_file = state_file.parent / f"setup-url-{args.port}"
    database = state_file.parent / "bridge.db"
    diagnostics = DiagnosticLog(state_file.parent / "diagnostics.jsonl")
    diagnostics.event("LOCAL_BRIDGE", "STARTED")
    host_for_url = "127.0.0.1" if args.host in {"::1", "localhost"} else args.host
    if bridge_is_running(args.host, args.port):
        print("CaptchaMesh 本地加密桥已在运行", flush=True)
        if setup_token_file.is_file():
            setup_token = read_private_text(setup_token_file)
            print_setup_url(
                f"http://{host_for_url}:{args.port}/setup#{setup_token}",
                force=args.show_setup_url,
                private_file=setup_url_file,
            )
        print(f"2Captcha v2 地址：http://{host_for_url}:{args.port}", flush=True)
        return
    require_available_port(args.host, args.port)
    local_key = ensure_private_key(local_key_file)
    setup_token = secrets.token_urlsafe(24)
    write_private_text(setup_token_file, setup_token)
    bootstrap_key_file = args.api_key_file
    default_bootstrap = state_file.parent / "hub-api.key"
    if bootstrap_key_file is None and default_bootstrap.is_file():
        bootstrap_key_file = default_bootstrap
    bootstrap_key = _read_secret(bootstrap_key_file)
    pairing = PairingManager(
        hub=args.hub,
        state_file=state_file,
        api_key=bootstrap_key,
        node_name=args.name,
    )
    if not state_file.is_file():
        try:
            await asyncio.to_thread(pairing.restart)
        except Exception as exc:
            diagnostics.event("LOCAL_BRIDGE", "INITIAL_PAIRING_FAILED", exc)
            pairing.last_error = str(exc)
    bridge = make_local_bridge(
        state_file=state_file,
        database=database,
        local_api_key=local_key,
        pairing=pairing,
        setup_token=setup_token,
        diagnostics=diagnostics,
    )
    setup_url = f"http://{host_for_url}:{args.port}/setup#{bridge.setup_token}"
    print("CaptchaMesh 本地加密桥已启动", flush=True)
    print_setup_url(
        setup_url,
        force=args.show_setup_url,
        private_file=setup_url_file,
    )
    print(f"2Captcha v2 地址：http://{host_for_url}:{args.port}", flush=True)
    print(f"本机 API Key：{local_key_file}（不会显示在终端）", flush=True)
    config = Config()
    config.bind = [f"{args.host}:{args.port}"]
    config.accesslog = None
    config.errorlog = "-"
    try:
        await serve(bridge.app, config)
    finally:
        setup_token_file.unlink(missing_ok=True)
        setup_url_file.unlink(missing_ok=True)


def show_config(args: argparse.Namespace) -> None:
    state_file = args.state_file.expanduser()
    key_file = state_file.parent / "local-api.key"
    if not key_file.is_file():
        raise SystemExit("CaptchaMesh 尚未初始化，请先运行 captchamesh start")
    value = {
        "apiBase": f"http://127.0.0.1:{args.port}",
        "apiKeyFile": str(key_file),
        "stateFile": str(state_file),
    }
    if args.show_secret:
        value["apiKey"] = read_private_text(key_file)
    if args.json:
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(f"API 地址：{value['apiBase']}")
        print(f"API Key 文件：{value['apiKeyFile']}")
        if args.show_secret:
            print(f"API Key：{value['apiKey']}")


def show_logs(args: argparse.Namespace) -> None:
    state_file = args.state_file.expanduser()
    diagnostics = DiagnosticLog(state_file.parent / "diagnostics.jsonl")
    if args.clear:
        diagnostics.clear()
        print("CaptchaMesh 本机诊断已清空")
        return
    value = diagnostics.read()
    if value:
        print(value, end="" if value.endswith("\n") else "\n")
    else:
        print("暂无本机诊断记录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="captchamesh", description="CaptchaMesh 本地加密桥")
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="启动本地兼容服务，未配对时生成二维码")
    start.add_argument("--hub", default=os.environ.get("CAPTCHAMESH_URL", "https://mesh.vimalinx.com"))
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8893)
    start.add_argument("--state-file", type=Path, default=default_state_file())
    start.add_argument("--api-key-file", type=Path)
    start.add_argument("--name", default=socket.gethostname())
    start.add_argument(
        "--show-setup-url",
        action="store_true",
        help="即使输出被重定向，也显示包含一次性能力令牌的配对链接",
    )

    config = subparsers.add_parser("config", help="输出 Agent 接入配置")
    config.add_argument("--port", type=int, default=8893)
    config.add_argument("--state-file", type=Path, default=default_state_file())
    config.add_argument("--json", action="store_true")
    config.add_argument(
        "--show-secret",
        action="store_true",
        help="显式把本机 API Key 输出到终端；默认只显示受限密钥文件路径",
    )

    logs = subparsers.add_parser("logs", help="查看脱敏的本机桥错误诊断")
    logs.add_argument("--state-file", type=Path, default=default_state_file())
    logs.add_argument("--clear", action="store_true", help="清空本机诊断记录")

    skill = subparsers.add_parser("skill", help="安装或检查 CaptchaMesh Agent Skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_install = skill_commands.add_parser("install", help="安装或安全更新 Agent Skill")
    skill_install.add_argument("--target", type=Path)
    skill_status_parser = skill_commands.add_parser("status", help="检查 Agent Skill 状态")
    skill_status_parser.add_argument("--target", type=Path)
    skill_status_parser.add_argument("--json", action="store_true")
    skill_inspect = skill_commands.add_parser(
        "inspect",
        help="检查目标项目的 CAPTCHA 接入信号",
        add_help=False,
    )
    skill_inspect.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:] or ["start"]
    args = parser.parse_args(argv)
    if args.command == "config":
        show_config(args)
        return 0
    if args.command == "logs":
        show_logs(args)
        return 0
    if args.command == "skill":
        try:
            if args.skill_command == "inspect":
                return run_bundled_inspector(args.arguments)
            if args.skill_command == "install":
                action, status = install_skill(target=args.target)
                if action == "current":
                    print(format_status(status))
                else:
                    label = "已安装" if action == "installed" else "已安全更新"
                    print(f"CaptchaMesh Agent Skill {label}：{status.target}")
                return 0
            status = skill_status(target=args.target)
            print(status.to_json() if args.json else format_status(status))
            return 0 if status.state == "current" else 1
        except SkillInstallError as exc:
            raise SystemExit(str(exc)) from exc
    asyncio.run(run_start(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
