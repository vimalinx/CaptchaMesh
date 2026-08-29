from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from relay_client import RelayClient
from relay_protocol import b64url_encode, decrypt_payload, encrypt_payload


class InMemoryConcurrentRelayClient(RelayClient):
    def __init__(self, state_file: Path) -> None:
        super().__init__(state_file)
        self._transport_lock = threading.Lock()
        self._sent: list[tuple[str, str]] = []
        self._result_envelopes: list[dict] = []
        self._results_built = False
        self.acked: list[str] = []

    def _post(self, path: str, value: dict, *, timeout: float):
        del timeout
        with self._transport_lock:
            if path == "/v1/relay/messages":
                payload = decrypt_payload(
                    self.pair_secret, value, expected_direction="node_to_phone"
                )
                self._sent.append(
                    (payload["taskId"], payload["task"]["websiteURL"])
                )
                return 201, {}
            if path == "/v1/relay/ack":
                self.acked.append(value["messageId"])
                return 200, {}
            if path != "/v1/relay/poll":
                raise AssertionError(path)
            if len(self._sent) < 2:
                return 204, {}
            if not self._results_built:
                for task_id, website_url in reversed(self._sent):
                    marker = website_url.rsplit("/", 1)[-1]
                    self._result_envelopes.append(
                        encrypt_payload(
                            self.pair_secret,
                            self.mailbox_id,
                            "phone_to_node",
                            {
                                "kind": "captcha_result",
                                "taskId": task_id,
                                "status": "ready",
                                "solution": {"token": "token-" + marker + "-" + "x" * 24},
                            },
                        )
                    )
                self._results_built = True
            if not self._result_envelopes:
                return 204, {}
            return 200, self._result_envelopes.pop(0)


class RelayClientConcurrencyTest(unittest.TestCase):
    def test_out_of_order_results_are_dispatched_to_the_matching_waiter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = b"c" * 32
            state_file = Path(directory) / "relay.json"
            state_file.write_text(
                json.dumps(
                    {
                        "hub": "https://mesh.example.test",
                        "mailboxId": "mailbox-test",
                        "deviceToken": "device-test",
                        "pairSecret": b64url_encode(secret),
                    }
                ),
                encoding="utf-8",
            )
            client = InMemoryConcurrentRelayClient(state_file)

            def solve(marker: str) -> dict:
                return client.solve(
                    {
                        "type": "turnstile",
                        "websiteURL": "https://example.test/" + marker,
                        "websiteKey": "site-key",
                        "timeoutSeconds": 30,
                    },
                    timeout=5,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(solve, "first")
                second = pool.submit(solve, "second")
                self.assertEqual(first.result()["token"], "token-first-" + "x" * 24)
                self.assertEqual(second.result()["token"], "token-second-" + "x" * 24)

            self.assertEqual(len(client.acked), 2)


if __name__ == "__main__":
    unittest.main()
