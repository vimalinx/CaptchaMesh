"""Non-solving smoke test for deployed CaptchaMesh 2Captcha v1/v2 endpoints."""
from __future__ import annotations

import argparse
from pathlib import Path

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        raise SystemExit("API key file is empty")

    health = requests.get(base_url + "/healthz", timeout=10).json()
    balance = requests.post(
        base_url + "/getBalance", json={"clientKey": api_key}, timeout=10
    ).json()
    missing = requests.post(
        base_url + "/getTaskResult",
        json={"clientKey": api_key, "taskId": 2_147_483_647},
        timeout=10,
    ).json()
    callback = requests.post(
        base_url + "/createTask",
        json={
            "clientKey": api_key,
            "callbackUrl": "https://example.invalid/callback",
            "task": {"type": "TurnstileTaskProxyless"},
        },
        timeout=10,
    ).json()
    v1_balance = requests.get(
        base_url + "/res.php",
        params={"key": api_key, "action": "getbalance", "json": 1},
        timeout=10,
    ).json()
    v1_bad_id = requests.get(
        base_url + "/res.php",
        params={"key": api_key, "action": "get", "id": "bad"},
        timeout=10,
    ).text
    v1_callback = requests.post(
        base_url + "/in.php",
        data={
            "key": api_key,
            "method": "turnstile",
            "sitekey": "not-enqueued",
            "pageurl": "https://example.invalid/register",
            "pingback": "https://example.invalid/callback",
        },
        timeout=10,
    ).text

    assert health.get("ok") is True
    assert balance.get("errorId") == 0 and float(balance["balance"]) > 0
    assert missing.get("errorCode") == "ERROR_TASK_NOT_FOUND"
    assert callback.get("errorCode") == "ERROR_CALLBACK_NOT_SUPPORTED"
    assert v1_balance.get("status") == 1 and float(v1_balance["request"]) > 0
    assert v1_bad_id == "ERROR_WRONG_ID_FORMAT"
    assert v1_callback == "ERROR_CALLBACK_NOT_SUPPORTED"
    print("health=ok v1=ok v2=ok auth=ok numeric_task_lookup=ok callback_guard=ok")


if __name__ == "__main__":
    main()
