#!/usr/bin/env python3
"""Pair one computer with the CaptchaMesh Android app."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
# Fixed argv, no shell, and pairing data is sent only over stdin.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

import requests

from relay_protocol import b64url_encode, build_pairing_uri, generate_pair_secret, validate_hub_url


DEFAULT_STATE = Path(".secrets/relay-pairing.json")


def _read_secret(path: Path | None) -> str:
    if path is None:
        return os.environ.get("CAPTCHAMESH_API_KEY", "").strip()
    return path.read_text(encoding="utf-8").strip()


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, (json.dumps(value, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    path.chmod(0o600)


def start_pairing(
    hub: str,
    *,
    api_key: str,
    state_file: Path,
    node_name: str,
    timeout: float = 20,
) -> str:
    hub = validate_hub_url(hub)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    response = requests.post(
        hub + "/v1/pairing/start",
        headers=headers,
        json={"nodeName": node_name},
        timeout=timeout,
    )
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Hub returned HTTP {response.status_code} without JSON") from exc
    if response.status_code != 201:
        raise RuntimeError(result.get("errorDescription", f"Hub returned HTTP {response.status_code}"))

    pair_secret = generate_pair_secret()
    pairing_uri = build_pairing_uri(
        hub_url=hub,
        mailbox_id=result["mailboxId"],
        join_token=result["joinToken"],
        pair_secret=pair_secret,
        node_name=node_name,
    )
    _write_private_json(
        state_file,
        {
            "protocolVersion": 1,
            "hub": hub,
            "mailboxId": result["mailboxId"],
            "deviceId": result["nodeDeviceId"],
            "deviceToken": result["nodeToken"],
            "pairSecret": b64url_encode(pair_secret),
            "nodeName": node_name,
        },
    )
    return pairing_uri


def show_qr(pairing_uri: str) -> None:
    executable = shutil.which("qrencode")
    if executable is None:
        raise RuntimeError("未安装 qrencode，无法在终端显示二维码")
    try:
        # The executable is resolved to an absolute path and argv is fixed.
        subprocess.run(  # nosec B603
            [executable, "-t", "ANSIUTF8", "-o", "-"],
            input=pairing_uri.encode("utf-8"),
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未安装 qrencode，无法在终端显示二维码") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="为 CaptchaMesh 手机端生成一次性配对二维码")
    parser.add_argument("--hub", default=os.environ.get("CAPTCHAMESH_URL", "https://mesh.vimalinx.com"))
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--name", default=socket.gethostname())
    args = parser.parse_args()
    uri = start_pairing(
        args.hub,
        api_key=_read_secret(args.api_key_file),
        state_file=args.state_file,
        node_name=args.name,
    )
    print("请在 60 秒内用系统相机扫描；密钥只存在二维码和两端设备中。")
    show_qr(uri)
    print(f"电脑端配对信息已保存到 {args.state_file}（权限 0600）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
