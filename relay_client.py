#!/usr/bin/env python3
"""End-to-end encrypted client for a paired CaptchaMesh phone."""
from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from broker import validate_task
from challenge_protocol import normalize_solution
from relay_protocol import b64url_decode, decrypt_payload, encrypt_payload, validate_hub_url


class RelayClientError(RuntimeError):
    pass


class RelayClient:
    def __init__(self, state_file: str | Path = ".secrets/relay-pairing.json") -> None:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
        self.hub = validate_hub_url(state["hub"])
        self.mailbox_id = state["mailboxId"]
        self.device_token = state["deviceToken"]
        self.pair_secret = b64url_decode(state["pairSecret"], name="pairSecret", expected_bytes=32)
        self.http = requests.Session()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": "Device " + self.device_token}

    def _post(self, path: str, value: dict[str, Any], *, timeout: float) -> tuple[int, dict[str, Any]]:
        try:
            response = self.http.post(
                self.hub + path, headers=self._headers, json=value, timeout=timeout
            )
            data = {} if response.status_code == 204 else response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RelayClientError(str(exc)) from exc
        if response.status_code >= 400:
            raise RelayClientError(data.get("errorDescription", f"Hub returned HTTP {response.status_code}"))
        return response.status_code, data

    def status(self, *, timeout: float = 10) -> dict[str, Any]:
        """Return non-secret mailbox status from the relay Hub."""
        try:
            response = self.http.get(
                self.hub + "/v1/relay/status", headers=self._headers, timeout=timeout
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RelayClientError(str(exc)) from exc
        if response.status_code >= 400:
            raise RelayClientError(
                data.get("errorDescription", f"Hub returned HTTP {response.status_code}")
            )
        return data

    def solve(self, task: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        public_task, context, assets = validate_task(task)
        task_id = "local-" + uuid.uuid4().hex
        payload = {
            "kind": "captcha_task",
            "taskId": task_id,
            "task": public_task,
            "context": context,
            "assets": {
                asset_id: {
                    "mediaType": media_type,
                    "data": base64.b64encode(content).decode("ascii"),
                }
                for asset_id, (content, media_type) in assets.items()
            },
        }
        envelope = encrypt_payload(self.pair_secret, self.mailbox_id, "node_to_phone", payload)
        self._post("/v1/relay/messages", envelope, timeout=20)

        wait_seconds = timeout if timeout is not None else int(public_task["timeoutSeconds"]) + 30
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            status, result_envelope = self._post(
                "/v1/relay/poll", {"waitSeconds": min(15, remaining)}, timeout=min(25, remaining + 5)
            )
            if status == 204:
                continue
            result = decrypt_payload(
                self.pair_secret, result_envelope, expected_direction="phone_to_node"
            )
            self._post(
                "/v1/relay/ack", {"messageId": result_envelope["messageId"]}, timeout=20
            )
            if result.get("taskId") != task_id:
                continue
            if result.get("status") != "ready":
                raise RelayClientError(result.get("errorDescription", "手机未完成验证"))
            return normalize_solution(public_task["type"], public_task, result.get("solution"))
        raise RelayClientError("等待手机验证超时")
