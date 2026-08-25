#!/usr/bin/env python3
"""Register a fixed local launcher allowlist with a CaptchaMesh v2 hub."""
from __future__ import annotations

import argparse
import os
import platform
import signal
# Process execution is deliberate and restricted to the machine-local allowlist.
import subprocess  # nosec B404
import threading
import time
from pathlib import Path
from typing import Any

import requests

from broker import PROTOCOL_VERSION, load_registrations


class AgentError(RuntimeError):
    def __init__(self, code: str, description: str, http_status: int = 0):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.http_status = http_status


class NodeAgent:
    def __init__(
        self,
        hub_url: str,
        api_key: str,
        node_key: str,
        node_id: str,
        name: str,
        registry_path: Path,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self.api_key = api_key
        self.node_key = node_key
        self.node_id = node_id
        self.name = name
        self.registry_path = registry_path
        self.registrations = load_registrations(registry_path)
        self.node_token = ""  # nosec B105
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.stopping: set[str] = set()
        self.process_lock = threading.Lock()
        self.shutdown = threading.Event()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        authorization: str,
        timeout: float = 35,
    ) -> dict[str, Any] | None:
        try:
            response = requests.request(
                method,
                self.hub_url + path,
                json=json_body,
                headers={"Accept": "application/json", "Authorization": authorization},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AgentError("ERROR_TRANSPORT", str(exc)) from exc
        if response.status_code == 204:
            return None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            raise AgentError(
                str(payload.get("errorCode", f"HTTP_{response.status_code}")),
                str(payload.get("errorDescription", "hub request failed")),
                response.status_code,
            )
        return payload

    def _offers(self) -> list[dict[str, Any]]:
        offers: list[dict[str, Any]] = []
        for registration in self.registrations.values():
            offers.append(
                {
                    "id": registration["id"],
                    "name": registration["name"],
                    "summary": registration["summary"],
                    "provides": registration["provides"],
                    "details": registration["details"],
                    "description": registration["description"],
                    "captchaTypes": registration["captchaTypes"],
                    "enabled": bool(
                        registration["enabled"] and registration["cwd"].is_dir()
                    ),
                }
            )
        return offers

    def join(self) -> None:
        payload = self._request(
            "POST",
            "/v1/nodes/join",
            json_body={
                "nodeId": self.node_id,
                "name": self.name,
                "version": PROTOCOL_VERSION,
                "device": platform.node(),
                "registrations": self._offers(),
            },
            authorization=f"NodeKey {self.node_key}",
        )
        if not payload or not payload.get("nodeToken"):
            raise AgentError("ERROR_BAD_JOIN", "hub did not return a node token")
        self.node_token = str(payload["nodeToken"])
        print(f"joined node={self.node_id} registrations={payload.get('registered', 0)}")

    def poll(self) -> dict[str, Any] | None:
        return self._request(
            "POST",
            "/v1/nodes/poll",
            json_body={"waitSeconds": 25},
            authorization=f"Node {self.node_token}",
            timeout=35,
        )

    def report(
        self,
        command_id: str,
        run_id: str,
        status: str,
        exit_code: int | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "commandId": command_id,
            "runId": run_id,
            "status": status,
        }
        if exit_code is not None:
            body["exitCode"] = exit_code
        self._request(
            "POST",
            "/v1/nodes/report",
            json_body=body,
            authorization=f"Node {self.node_token}",
        )

    def _watch(self, command_id: str, run_id: str, process: subprocess.Popen[bytes]) -> None:
        exit_code = process.wait()
        with self.process_lock:
            cancelled = run_id in self.stopping
            self.processes.pop(run_id, None)
            self.stopping.discard(run_id)
        status = "cancelled" if cancelled else ("succeeded" if exit_code == 0 else "failed")
        for attempt in range(3):
            try:
                self.report(command_id, run_id, status, exit_code)
                print(f"finished run={run_id} status={status} exit={exit_code}")
                return
            except AgentError as exc:
                if exc.http_status == 401:
                    self.node_token = ""  # nosec B105
                    try:
                        self.join()
                    except AgentError:
                        pass
                if attempt == 2:
                    print(f"report failed run={run_id} code={exc.code}")
                    return
                time.sleep(2**attempt)

    def start(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("commandId", ""))
        run_id = str(command.get("runId", ""))
        registration_id = str(command.get("registrationId", ""))
        registration = self.registrations.get(registration_id)
        if registration is None or not registration["enabled"]:
            self.report(command_id, run_id, "failed", 127)
            return
        with self.process_lock:
            if run_id in self.processes:
                self.report(command_id, run_id, "running")
                return
        environment = os.environ.copy()
        environment.update(
            {
                "CAPTCHAMESH_URL": self.hub_url,
                "CAPTCHAMESH_API_KEY": self.api_key,
                "CAPTCHAMESH_RUN_ID": run_id,
                "CAPTCHAMESH_REGISTRATION_ID": registration_id,
            }
        )
        project_root = str(Path(__file__).resolve().parent)
        existing_python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = project_root + (
            os.pathsep + existing_python_path if existing_python_path else ""
        )
        try:
            # The argv and cwd come only from registrations.json; no shell is used.
            process = subprocess.Popen(  # nosec B603
                registration["command"],
                cwd=registration["cwd"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError):
            self.report(command_id, run_id, "failed", 126)
            return
        with self.process_lock:
            self.processes[run_id] = process
        self.report(command_id, run_id, "running")
        threading.Thread(
            target=self._watch,
            args=(command_id, run_id, process),
            name=f"captchamesh-{run_id[-8:]}",
            daemon=True,
        ).start()
        print(f"started run={run_id} registration={registration_id}")

    def stop(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("commandId", ""))
        run_id = str(command.get("runId", ""))
        with self.process_lock:
            process = self.processes.get(run_id)
            if process is not None:
                self.stopping.add(run_id)
        if process is None or process.poll() is not None:
            self.report(command_id, run_id, "cancelled")
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.report(command_id, run_id, "cancelled")

    def run(self) -> None:
        backoff = 1
        while not self.shutdown.is_set():
            try:
                if not self.node_token:
                    self.join()
                command = self.poll()
                backoff = 1
                if command is None:
                    continue
                action = command.get("action")
                if action == "start":
                    self.start(command)
                elif action == "stop":
                    self.stop(command)
            except AgentError as exc:
                if exc.http_status == 401:
                    self.node_token = ""  # nosec B105
                print(f"node loop error code={exc.code}; retrying")
                self.shutdown.wait(backoff)
                backoff = min(backoff * 2, 20)

    def close(self) -> None:
        self.shutdown.set()
        with self.process_lock:
            running = list(self.processes.items())
            self.stopping.update(run_id for run_id, _ in running)
        for _, process in running:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + 5
        for _, process in running:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def _read_secret(path: str | None, env_name: str) -> str:
    if path:
        value = Path(path).expanduser().read_text().strip()
    else:
        value = os.environ.get(env_name, "").strip()
    if not value:
        raise SystemExit(f"{env_name} or its key file is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Advertise fixed registration launchers to a hub")
    parser.add_argument("--hub", default=os.environ.get("CAPTCHAMESH_URL", ""))
    parser.add_argument("--api-key-file")
    parser.add_argument("--node-key-file")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--registry", type=Path, default=Path(__file__).parent / "registrations.json")
    args = parser.parse_args()
    if not args.hub:
        parser.error("--hub or CAPTCHAMESH_URL is required")
    agent = NodeAgent(
        args.hub,
        _read_secret(args.api_key_file, "CAPTCHAMESH_API_KEY"),
        _read_secret(args.node_key_file, "CAPTCHAMESH_NODE_KEY"),
        args.node_id,
        args.name,
        args.registry,
    )
    try:
        agent.run()
    except KeyboardInterrupt:
        pass
    finally:
        agent.close()


if __name__ == "__main__":
    main()
