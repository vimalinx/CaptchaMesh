#!/usr/bin/env python3
"""Installable command-line entry point for the CaptchaMesh local bridge."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
from pathlib import Path

import requests
from hypercorn.asyncio import serve
from hypercorn.config import Config

from local_bridge import PairingManager, make_local_bridge
from pair_device import _read_secret


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
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError(f"本机 API Key 文件为空：{path}")
        path.chmod(0o600)
        return value
    value = "local-" + secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (value + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    return value


def write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, (value + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    path.chmod(0o600)


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


async def run_start(args: argparse.Namespace) -> None:
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("本地兼容桥只允许监听 loopback 地址")
    state_file = args.state_file.expanduser()
    state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    local_key_file = state_file.parent / "local-api.key"
    setup_token_file = state_file.parent / f"setup-token-{args.port}"
    database = state_file.parent / "bridge.db"
    host_for_url = "127.0.0.1" if args.host in {"::1", "localhost"} else args.host
    if bridge_is_running(args.host, args.port):
        print("CaptchaMesh 本地加密桥已在运行", flush=True)
        if setup_token_file.is_file():
            setup_token = setup_token_file.read_text(encoding="utf-8").strip()
            print(
                f"配对与状态：http://{host_for_url}:{args.port}/setup/{setup_token}",
                flush=True,
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
            pairing.last_error = str(exc)
    bridge = make_local_bridge(
        state_file=state_file,
        database=database,
        local_api_key=local_key,
        pairing=pairing,
        setup_token=setup_token,
    )
    setup_url = f"http://{host_for_url}:{args.port}/setup/{bridge.setup_token}"
    print("CaptchaMesh 本地加密桥已启动", flush=True)
    print(f"配对与状态：{setup_url}", flush=True)
    print(f"2Captcha v2 地址：http://{host_for_url}:{args.port}", flush=True)
    print(f"本机 API Key：{local_key_file}（不会显示在终端）", flush=True)
    config = Config()
    config.bind = [f"{args.host}:{args.port}"]
    config.accesslog = None
    config.errorlog = "-"
    await serve(bridge.app, config)


def show_config(args: argparse.Namespace) -> None:
    state_file = args.state_file.expanduser()
    key_file = state_file.parent / "local-api.key"
    if not key_file.is_file():
        raise SystemExit("CaptchaMesh 尚未初始化，请先运行 captchamesh start")
    value = {
        "apiBase": f"http://127.0.0.1:{args.port}",
        "apiKey": key_file.read_text(encoding="utf-8").strip(),
        "stateFile": str(state_file),
    }
    if args.json:
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(f"API 地址：{value['apiBase']}")
        print(f"API Key：{value['apiKey']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="captchamesh", description="CaptchaMesh 本地加密桥")
    parser.set_defaults(command="start")
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="启动本地兼容服务，未配对时生成二维码")
    start.add_argument("--hub", default=os.environ.get("CAPTCHAMESH_URL", "https://mesh.vimalinx.com"))
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8893)
    start.add_argument("--state-file", type=Path, default=default_state_file())
    start.add_argument("--api-key-file", type=Path)
    start.add_argument("--name", default=socket.gethostname())

    config = subparsers.add_parser("config", help="输出 Agent 接入配置")
    config.add_argument("--port", type=int, default=8893)
    config.add_argument("--state-file", type=Path, default=default_state_file())
    config.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "config":
        show_config(args)
        return 0
    asyncio.run(run_start(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
