from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import broker


class NodeProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        registry = root / "registrations.json"
        registry.write_text("[]", encoding="utf-8")
        broker.DB_PATH = root / "broker.db"
        broker.REGISTRY_PATH = registry
        self.app = broker.make_app(api_key="phone-secret", node_key="node-secret")
        self.client = self.app.test_client()

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def join_node(self) -> str:
        response = await self.client.post(
            "/v1/nodes/join",
            headers={"Authorization": "NodeKey node-secret"},
            json={
                "nodeId": "office-pc",
                "name": "办公室电脑",
                "version": "2",
                "device": "test",
                "registrations": [
                    {
                        "id": "vinci",
                        "name": "Vinci",
                        "summary": "注册 1 个 Vinci 账号并签发三类 Key",
                        "provides": ["Vinci 账号", "三类 API Key"],
                        "details": [
                            {"label": "模型", "value": "GLM-5.2 / DeepSeek V4 Flash / Kimi K3"}
                        ],
                        "captchaTypes": [],
                        "enabled": True,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        return (await response.get_json())["nodeToken"]

    async def test_remote_registration_start_report_and_stop(self) -> None:
        node_token = await self.join_node()
        response = await self.client.get(
            "/v1/registrations",
            headers={"Authorization": "Bearer phone-secret"},
        )
        payload = await response.get_json()
        self.assertEqual(response.status_code, 200)
        registration = payload["registrations"][0]
        self.assertEqual(registration["id"], "office-pc:vinci")
        self.assertEqual(registration["source"], "办公室电脑")
        self.assertEqual(registration["details"][0]["label"], "模型")
        self.assertTrue(registration["enabled"])

        response = await self.client.post(
            "/v1/registrations/office-pc:vinci/start",
            headers={"Authorization": "Bearer phone-secret"},
            json={},
        )
        self.assertEqual(response.status_code, 201)
        run_id = (await response.get_json())["run"]["runId"]

        response = await self.client.post(
            "/v1/nodes/poll",
            headers={"Authorization": f"Node {node_token}"},
            json={"waitSeconds": 0},
        )
        command = await response.get_json()
        self.assertEqual(command["action"], "start")
        self.assertEqual(command["registrationId"], "vinci")
        self.assertEqual(command["runId"], run_id)

        response = await self.client.post(
            "/v1/nodes/report",
            headers={"Authorization": f"Node {node_token}"},
            json={
                "commandId": command["commandId"],
                "runId": run_id,
                "status": "running",
            },
        )
        self.assertEqual(response.status_code, 200)

        response = await self.client.post(
            f"/v1/runs/{run_id}/stop",
            headers={"Authorization": "Bearer phone-secret"},
            json={},
        )
        self.assertEqual(response.status_code, 200)

        response = await self.client.post(
            "/v1/nodes/poll",
            headers={"Authorization": f"Node {node_token}"},
            json={"waitSeconds": 0},
        )
        stop_command = await response.get_json()
        self.assertEqual(stop_command["action"], "stop")
        self.assertEqual(stop_command["runId"], run_id)

        response = await self.client.post(
            "/v1/nodes/report",
            headers={"Authorization": f"Node {node_token}"},
            json={
                "commandId": stop_command["commandId"],
                "runId": run_id,
                "status": "cancelled",
                "exitCode": -15,
            },
        )
        self.assertEqual(response.status_code, 200)
        response = await self.client.get(
            f"/v1/runs/{run_id}",
            headers={"Authorization": "Bearer phone-secret"},
        )
        self.assertEqual((await response.get_json())["run"]["status"], "cancelled")

    async def test_offline_node_is_visible_but_not_startable(self) -> None:
        await self.join_node()
        with broker.db() as connection:
            connection.execute(
                "UPDATE nodes SET last_seen=? WHERE id='office-pc'",
                (time.time() - broker.NODE_ONLINE_SECONDS - 1,),
            )
        response = await self.client.get(
            "/v1/registrations",
            headers={"Authorization": "Bearer phone-secret"},
        )
        registration = (await response.get_json())["registrations"][0]
        self.assertFalse(registration["online"])
        self.assertFalse(registration["enabled"])
        response = await self.client.post(
            "/v1/registrations/office-pc:vinci/start",
            headers={"Authorization": "Bearer phone-secret"},
            json={},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await response.get_json())["errorCode"], "ERROR_NODE_OFFLINE")

    async def test_join_requires_separate_node_key(self) -> None:
        response = await self.client.post(
            "/v1/nodes/join",
            headers={"Authorization": "NodeKey wrong"},
            json={"nodeId": "office-pc", "name": "办公室电脑", "registrations": []},
        )
        self.assertEqual(response.status_code, 401)

    async def test_server_rejects_reused_phone_and_node_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be different"):
            broker.make_app(api_key="reused-secret", node_key="reused-secret")


if __name__ == "__main__":
    unittest.main()
