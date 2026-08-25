from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from captchamesh import TwoCaptcha
from captchamesh_cli import (
    bridge_is_running,
    default_state_file,
    ensure_private_key,
    require_available_port,
    write_private_text,
)
from local_bridge import LocalBridge, PairingManager


class FakeRelayClient:
    active = 0
    max_active = 0

    def __init__(self, _state_file: Path) -> None:
        pass

    def status(self) -> dict:
        return {
            "queued": 0,
            "devices": [
                {"role": "node", "name": "Test Agent"},
                {"role": "phone", "name": "Test Phone"},
            ],
        }

    def solve(self, task: dict) -> dict:
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            import time

            time.sleep(0.02)
            if task["type"] == "image_text":
                return {"text": "42"}
            return {"token": "manual-token"}
        finally:
            type(self).active -= 1


class LocalBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.state = root / "relay-pairing.json"
        self.state.write_text("{}", encoding="utf-8")
        self.pairing = PairingManager(
            hub="https://mesh.example.test",
            state_file=self.state,
            api_key="bootstrap",
            node_name="Test Agent",
        )
        self.pairing.pairing_uri = (
            "captchamesh://pair?v=1&hub=https%3A%2F%2Fmesh.example.test&secret=sensitive"
        )
        self.bridge = LocalBridge(
            state_file=self.state,
            database=root / "bridge.db",
            local_api_key="local-test-key",
            pairing=self.pairing,
            client_factory=FakeRelayClient,
        )
        self.client = self.bridge.app.test_client()
        FakeRelayClient.active = 0
        FakeRelayClient.max_active = 0

    async def asyncTearDown(self) -> None:
        if self.bridge._background:
            await asyncio.gather(*self.bridge._background, return_exceptions=True)
        self.tempdir.cleanup()

    async def wait_v2(self, task_id: int) -> dict:
        for _ in range(50):
            response = await self.client.post(
                "/getTaskResult",
                json={"clientKey": "local-test-key", "taskId": task_id},
            )
            payload = await response.get_json()
            if payload.get("status") == "ready" or payload.get("errorId"):
                return payload
            await asyncio.sleep(0.01)
        self.fail("bridge task did not finish")

    async def test_v2_round_trip_uses_standard_solution_shape(self) -> None:
        created = await self.client.post(
            "/createTask",
            json={
                "clientKey": "local-test-key",
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": "https://example.test/",
                    "websiteKey": "site-key",
                },
            },
        )
        body = await created.get_json()
        self.assertEqual(body["errorId"], 0)
        result = await self.wait_v2(body["taskId"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["solution"]["token"], "manual-token")

    async def test_v1_round_trip_and_key_rejection(self) -> None:
        rejected = await self.client.post(
            "/in.php",
            form={
                "key": "wrong",
                "method": "turnstile",
                "sitekey": "site-key",
                "pageurl": "https://example.test/",
            },
        )
        self.assertEqual(await rejected.get_data(as_text=True), "ERROR_KEY_DOES_NOT_EXIST")

        created = await self.client.post(
            "/in.php",
            form={
                "key": "local-test-key",
                "method": "turnstile",
                "sitekey": "site-key",
                "pageurl": "https://example.test/",
            },
        )
        task_id = int((await created.get_data(as_text=True)).removeprefix("OK|"))
        for _ in range(50):
            polled = await self.client.get(
                "/res.php",
                query_string={"key": "local-test-key", "action": "get", "id": task_id},
            )
            value = await polled.get_data(as_text=True)
            if value != "CAPCHA_NOT_READY":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(value, "OK|manual-token")

    async def test_tasks_are_serialized_for_one_phone_mailbox(self) -> None:
        task_ids = []
        for index in range(2):
            response = await self.client.post(
                "/createTask",
                json={
                    "clientKey": "local-test-key",
                    "task": {
                        "type": "TurnstileTaskProxyless",
                        "websiteURL": f"https://example.test/{index}",
                        "websiteKey": "site-key",
                    },
                },
            )
            task_ids.append((await response.get_json())["taskId"])
        await asyncio.gather(*(self.wait_v2(task_id) for task_id in task_ids))
        self.assertEqual(FakeRelayClient.max_active, 1)

    async def test_setup_page_hides_pairing_secret_and_reports_phone(self) -> None:
        prefix = "/setup/" + self.bridge.setup_token
        page = await self.client.get(prefix)
        source = await page.get_data(as_text=True)
        self.assertNotIn("sensitive", source)
        self.assertIn("用手机扫描", source)
        self.assertIn("alt=\"CaptchaMesh 一次性配对二维码\"", source)
        status = await self.client.get(prefix + "/status")
        self.assertEqual((await status.get_json())["phoneName"], "Test Phone")
        qr = await self.client.get(prefix + "/pairing.svg")
        self.assertEqual(qr.content_type, "image/svg+xml")

    async def test_completed_results_expire_from_local_database(self) -> None:
        task_id = self.bridge.store.create(
            "v2",
            {"type": "turnstile"},
            "TurnstileTaskProxyless",
        )
        self.bridge.store.set_solution(task_id, {"token": "short-lived"})
        with self.bridge.store.connect() as connection:
            connection.execute(
                "UPDATE bridge_tasks SET updated_at=0 WHERE id=?", (task_id,)
            )
        self.assertIsNone(self.bridge.store.get(task_id))

    async def test_pairing_regeneration_requires_setup_capability(self) -> None:
        prefix = "/setup/" + self.bridge.setup_token
        forbidden = await self.client.post(prefix + "/pair")
        self.assertEqual(forbidden.status_code, 403)

        def restart() -> str:
            self.pairing.pairing_uri = "captchamesh://pair?v=1&secret=new"
            return self.pairing.pairing_uri

        with patch.object(self.pairing, "restart", side_effect=restart) as called:
            accepted = await self.client.post(
                prefix + "/pair",
                headers={"X-CaptchaMesh-CSRF": self.bridge.setup_token},
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue((await accepted.get_json())["ok"])
        called.assert_called_once_with()


class LocalKeyTest(unittest.TestCase):
    def test_local_key_is_stable_and_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "local-api.key"
            first = ensure_private_key(path)
            second = ensure_private_key(path)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("local-"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_setup_capability_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "setup-token"
            write_private_text(path, "capability")
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "capability")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_local_bridge_is_detected_idempotently(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {"service": "captchamesh-local-bridge"}
        with patch("captchamesh_cli.requests.Session.get", return_value=response) as get:
            self.assertTrue(bridge_is_running("127.0.0.1", 8893))
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8893/healthz")

    def test_installed_global_pairing_state_wins_over_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"XDG_CONFIG_HOME": directory}, clear=False
        ):
            state = Path(directory) / "captchamesh" / "relay-pairing.json"
            state.parent.mkdir()
            state.write_text("{}", encoding="utf-8")
            self.assertEqual(default_state_file(), state)

    def test_upstream_sdk_wrapper_uses_loopback_http(self) -> None:
        submitted = Mock(status_code=200, content=b"OK|17")
        completed = Mock(status_code=200, content=b"OK|manual-token")
        with patch("captchamesh.twocaptcha.requests.post", return_value=submitted) as post, patch(
            "captchamesh.twocaptcha.requests.get", return_value=completed
        ) as get:
            solver = TwoCaptcha("local-test-key", pollingInterval=0)
            result = solver.turnstile(sitekey="site-key", url="https://example.test/")
        self.assertEqual(result["code"], "manual-token")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8893/in.php")
        self.assertEqual(post.call_args.kwargs["data"]["pageurl"], "https://example.test/")
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8893/res.php")

    def test_sdk_wrapper_accepts_existing_server_option(self) -> None:
        solver = TwoCaptcha("local-test-key", server="127.0.0.1:9911")
        self.assertEqual(solver.api_client.endpoint, "http://127.0.0.1:9911")

    def test_sdk_wrapper_rejects_non_loopback_endpoint(self) -> None:
        for endpoint in (
            "http://example.test:8891",
            "https://127.0.0.1:8891",
            "http://localhost:8891@example.test",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "loopback"):
                TwoCaptcha("local-test-key", endpoint=endpoint)

    def test_startup_refuses_an_occupied_port_before_pairing(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        try:
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(SystemExit, "已被占用"):
                require_available_port("127.0.0.1", port)
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
