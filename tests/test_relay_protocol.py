from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import broker
import pair_device

from relay_protocol import (
    RelayProtocolError,
    build_pairing_uri,
    decrypt_payload,
    encrypt_payload,
    generate_pair_secret,
    parse_pairing_uri,
)


class RelayCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = bytes(range(32))

    def test_pairing_uri_round_trips_without_server_key_material(self) -> None:
        uri = build_pairing_uri(
            hub_url="https://mesh.example.com/",
            mailbox_id="mb-test",
            join_token="join-test",
            pair_secret=self.secret,
            node_name="My Laptop",
        )
        parsed = parse_pairing_uri(uri)
        self.assertEqual(parsed["hub"], "https://mesh.example.com")
        self.assertEqual(parsed["mailboxId"], "mb-test")
        self.assertEqual(parsed["pairSecret"], self.secret)
        self.assertEqual(parsed["nodeName"], "My Laptop")

    def test_http_pairing_is_only_allowed_for_loopback(self) -> None:
        local = build_pairing_uri(
            hub_url="http://127.0.0.1:8890",
            mailbox_id="mb-local",
            join_token="join-local",
            pair_secret=self.secret,
            node_name="Local",
        )
        self.assertEqual(parse_pairing_uri(local)["hub"], "http://127.0.0.1:8890")
        with self.assertRaises(RelayProtocolError):
            build_pairing_uri(
                hub_url="http://mesh.example.com",
                mailbox_id="mb-test",
                join_token="join-test",
                pair_secret=self.secret,
                node_name="Unsafe",
            )

    def test_hub_url_rejects_remote_nonstandard_port_and_path(self) -> None:
        for value in (
            "https://mesh.example.com:444",
            "https://mesh.example.com/hidden",
            "https://mesh.example.com:bad",
        ):
            with self.subTest(value=value), self.assertRaises(RelayProtocolError):
                build_pairing_uri(
                    hub_url=value,
                    mailbox_id="mb-test",
                    join_token="join-test",
                    pair_secret=self.secret,
                    node_name="Unsafe",
                )

    def test_pairing_state_is_written_with_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / ".secrets" / "pair.json"
            pair_device._write_private_json(state, {"deviceToken": "secret"})
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(state.read_text())["deviceToken"], "secret")

    def test_pairing_secret_is_created_on_endpoint_not_returned_by_hub(self) -> None:
        class Response:
            status_code = 201
            def json(self):
                return {
                    "mailboxId": "mb-test", "joinToken": "join-test",
                    "nodeDeviceId": "dev-node", "nodeToken": "node-token",
                }

        with tempfile.TemporaryDirectory() as directory, patch(
                "pair_device.requests.post", return_value=Response()):
            state = Path(directory) / "pair.json"
            uri = pair_device.start_pairing(
                "https://mesh.example.com", api_key="bootstrap",
                state_file=state, node_name="Laptop")
            saved = json.loads(state.read_text())
            self.assertIn("secret=", uri)
            self.assertEqual(len(saved["pairSecret"]), 43)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_envelope_round_trip_and_tamper_rejection(self) -> None:
        envelope = encrypt_payload(
            self.secret,
            "mb-test",
            "node_to_phone",
            {"kind": "captcha_task", "websiteURL": "https://private.example/"},
            message_id="msg-test",
            expires_at=1_600,
            now_value=1_000,
        )
        self.assertNotIn("private.example", str(envelope))
        decoded = decrypt_payload(
            self.secret,
            envelope,
            expected_direction="node_to_phone",
            now_value=1_001,
        )
        self.assertEqual(decoded["kind"], "captcha_task")

        tampered = dict(envelope, expiresAt=1_601)
        with self.assertRaises(RelayProtocolError):
            decrypt_payload(
                self.secret,
                tampered,
                expected_direction="node_to_phone",
                now_value=1_001,
            )
        with self.assertRaises(RelayProtocolError):
            decrypt_payload(
                self.secret,
                envelope,
                expected_direction="phone_to_node",
                now_value=1_001,
            )

    def test_expired_envelope_and_wrong_secret_are_rejected(self) -> None:
        envelope = encrypt_payload(
            self.secret,
            "mb-test",
            "phone_to_node",
            {"kind": "captcha_result"},
            expires_at=1_100,
            now_value=1_000,
        )
        with self.assertRaises(RelayProtocolError):
            decrypt_payload(
                self.secret,
                envelope,
                expected_direction="phone_to_node",
                now_value=1_100,
            )
        with self.assertRaises(RelayProtocolError):
            decrypt_payload(
                generate_pair_secret(),
                envelope,
                expected_direction="phone_to_node",
                now_value=1_001,
            )


class RelayBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        registry = root / "registrations.json"
        registry.write_text("[]", encoding="utf-8")
        broker.DB_PATH = root / "broker.db"
        broker.REGISTRY_PATH = registry
        self.app = broker.make_app(api_key="bootstrap-secret", node_key="node-secret")
        self.client = self.app.test_client()

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def pair(self) -> tuple[dict, dict]:
        started = await self.client.post(
            "/v1/pairing/start",
            headers={"Authorization": "Bearer bootstrap-secret"},
            json={"nodeName": "Personal Agent"},
        )
        self.assertEqual(started.status_code, 201)
        node = await started.get_json()
        claimed = await self.client.post(
            "/v1/pairing/claim",
            json={"joinToken": node["joinToken"], "phoneName": "Test Phone"},
        )
        self.assertEqual(claimed.status_code, 200)
        return node, await claimed.get_json()

    async def test_manifest_describes_endpoint_managed_encryption(self) -> None:
        response = await self.client.get("/.well-known/captchamesh")
        payload = await response.get_json()
        self.assertEqual(payload["protocolVersions"], [1])
        self.assertEqual(payload["encryption"], "endpoint-managed")

    async def test_pairing_uses_one_time_claim_and_hashed_tokens(self) -> None:
        node, phone = await self.pair()
        reused = await self.client.post(
            "/v1/pairing/claim",
            json={"joinToken": node["joinToken"], "phoneName": "Other Phone"},
        )
        self.assertEqual(reused.status_code, 410)
        with broker.db() as connection:
            device_rows = connection.execute(
                "SELECT role,token_hash FROM relay_devices ORDER BY role"
            ).fetchall()
            stored = " ".join(row["token_hash"] for row in device_rows)
        self.assertNotIn(node["nodeToken"], stored)
        self.assertNotIn(phone["deviceToken"], stored)
        self.assertEqual({row["role"] for row in device_rows}, {"node", "phone"})

    async def test_opaque_message_round_trip_ack_and_replay_protection(self) -> None:
        node, phone = await self.pair()
        secret = bytes(range(32))
        envelope = encrypt_payload(
            secret,
            node["mailboxId"],
            "node_to_phone",
            {
                "kind": "captcha_task",
                "websiteURL": "https://server-must-not-see.example/",
                "cookie": "private-cookie",
            },
        )
        sent = await self.client.post(
            "/v1/relay/messages",
            headers={"Authorization": f"Device {node['nodeToken']}"},
            json=envelope,
        )
        self.assertEqual(sent.status_code, 201)
        replay = await self.client.post(
            "/v1/relay/messages",
            headers={"Authorization": f"Device {node['nodeToken']}"},
            json=envelope,
        )
        self.assertEqual(replay.status_code, 409)

        with broker.db() as connection:
            row = connection.execute(
                "SELECT ciphertext,nonce FROM relay_messages WHERE message_id=?",
                (envelope["messageId"],),
            ).fetchone()
        self.assertNotIn("server-must-not-see", row["ciphertext"])
        self.assertNotIn("private-cookie", row["ciphertext"])

        polled = await self.client.post(
            "/v1/relay/poll",
            headers={"Authorization": f"Device {phone['deviceToken']}"},
            json={"waitSeconds": 0},
        )
        received = await polled.get_json()
        self.assertEqual(
            decrypt_payload(
                secret, received, expected_direction="node_to_phone"
            )["cookie"],
            "private-cookie",
        )
        acked = await self.client.post(
            "/v1/relay/ack",
            headers={"Authorization": f"Device {phone['deviceToken']}"},
            json={"messageId": received["messageId"]},
        )
        self.assertEqual(acked.status_code, 200)
        empty = await self.client.post(
            "/v1/relay/poll",
            headers={"Authorization": f"Device {phone['deviceToken']}"},
            json={"waitSeconds": 0},
        )
        self.assertEqual(empty.status_code, 204)

    async def test_device_role_cannot_reverse_message_direction(self) -> None:
        node, _ = await self.pair()
        envelope = encrypt_payload(
            bytes(range(32)),
            node["mailboxId"],
            "phone_to_node",
            {"kind": "forged"},
        )
        response = await self.client.post(
            "/v1/relay/messages",
            headers={"Authorization": f"Device {node['nodeToken']}"},
            json=envelope,
        )
        self.assertEqual(response.status_code, 403)


class PublicPairingBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        registry = root / "registrations.json"
        registry.write_text("[]", encoding="utf-8")
        broker.DB_PATH = root / "broker.db"
        broker.REGISTRY_PATH = registry
        self.app = broker.make_app(
            api_key="admin-secret",
            node_key="node-secret",
            allow_public_pairing=True,
        )
        self.client = self.app.test_client()

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_public_access_is_limited_to_pairing_start(self) -> None:
        manifest = await self.client.get("/.well-known/captchamesh")
        self.assertEqual((await manifest.get_json())["pairingAuth"], "optional")
        started = await self.client.post(
            "/v1/pairing/start", json={"nodeName": "New Personal Agent"}
        )
        self.assertEqual(started.status_code, 201)
        self.assertIn("joinToken", await started.get_json())

        invalid_header = await self.client.post(
            "/v1/pairing/start",
            headers={"Authorization": "Bearer wrong"},
            json={"nodeName": "Rejected Agent"},
        )
        self.assertEqual(invalid_header.status_code, 401)
        balance = await self.client.post("/getBalance", json={"clientKey": "wrong"})
        self.assertEqual((await balance.get_json())["errorCode"], "ERROR_KEY_DOES_NOT_EXIST")

    async def test_public_pairing_rate_limit_and_orphan_cleanup(self) -> None:
        first = await self.client.post(
            "/v1/pairing/start", json={"nodeName": "Agent 0"}
        )
        first_mailbox = (await first.get_json())["mailboxId"]
        with broker.db() as connection:
            connection.execute(
                "UPDATE relay_mailboxes SET created_at=0 WHERE id=?", (first_mailbox,)
            )
            connection.execute(
                "UPDATE relay_pairings SET expires_at=0 WHERE mailbox_id=?", (first_mailbox,)
            )
        for index in range(1, broker.PUBLIC_PAIRING_PER_MINUTE):
            response = await self.client.post(
                "/v1/pairing/start", json={"nodeName": f"Agent {index}"}
            )
            self.assertEqual(response.status_code, 201)
        limited = await self.client.post(
            "/v1/pairing/start", json={"nodeName": "One Too Many"}
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual((await limited.get_json())["errorCode"], "ERROR_PAIRING_RATE_LIMIT")
        with broker.db() as connection:
            old_mailbox = connection.execute(
                "SELECT id FROM relay_mailboxes WHERE id=?", (first_mailbox,)
            ).fetchone()
        self.assertIsNone(old_mailbox)


class BrokerSecurityBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        registry = root / "registrations.json"
        registry.write_text("[]", encoding="utf-8")
        broker.DB_PATH = root / "broker.db"
        broker.REGISTRY_PATH = registry

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_host_header_injection_is_rejected(self) -> None:
        app = broker.make_app(
            api_key="admin-secret",
            node_key="node-secret",
            allowed_hosts={"localhost", "mesh.vimalinx.com"},
        )
        response = await app.test_client().get(
            "/healthz", headers={"Host": "mesh.vimalinx.com:443@evil.example"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["errorCode"], "ERROR_BAD_HOST")

    async def test_security_headers_are_always_present(self) -> None:
        app = broker.make_app(api_key="admin-secret", node_key="node-secret")
        response = await app.test_client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    async def test_invalid_cloudflare_client_address_is_rejected(self) -> None:
        app = broker.make_app(api_key="admin-secret", node_key="node-secret")
        response = await app.test_client().get(
            "/healthz",
            headers={"CF-Connecting-IP": "127.0.0.1, 203.0.113.5"},
            scope_base={"client": ("127.0.0.1", 41000)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["errorCode"], "ERROR_BAD_PROXY_HEADER")

    async def test_per_client_rate_limit_is_enforced(self) -> None:
        app = broker.make_app(
            api_key="admin-secret",
            node_key="node-secret",
            requests_per_minute_per_client=2,
            requests_per_minute_global=100,
        )
        client = app.test_client()
        self.assertEqual((await client.get("/healthz")).status_code, 200)
        self.assertEqual((await client.get("/healthz")).status_code, 200)
        limited = await client.get("/healthz")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual((await limited.get_json())["errorCode"], "ERROR_RATE_LIMIT")

    async def test_request_body_limit_returns_structured_error(self) -> None:
        app = broker.make_app(api_key="admin-secret", node_key="node-secret")
        app.config["MAX_CONTENT_LENGTH"] = 64
        response = await app.test_client().post(
            "/v1/pairing/start",
            headers={"Authorization": "Bearer admin-secret"},
            data=b"{" + b"x" * 128 + b"}",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual((await response.get_json())["errorCode"], "ERROR_REQUEST_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
