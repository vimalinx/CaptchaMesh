from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

import broker
from challenge_protocol import SolutionError, normalize_solution
from twocaptcha_compat import translate_create_request, translate_solution


PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8"
    b"\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


class TypedSolutionTest(unittest.TestCase):
    def test_all_structured_solution_shapes(self) -> None:
        image_task = {"presentation": {"minLength": 2, "maxLength": 5}}
        self.assertEqual(normalize_solution("image_text", image_task, {"text": "AB"}), {"text": "AB"})
        self.assertEqual(
            normalize_solution("coordinates", {}, {"points": [{"x": 10, "y": 20}]}),
            {"coordinates": [{"x": 10, "y": 20}]},
        )
        grid_task = {"presentation": {"rows": 3, "columns": 3}}
        self.assertEqual(normalize_solution("grid", grid_task, {"cells": [1, 9]}), {"click": [1, 9]})
        self.assertEqual(normalize_solution("rotate", {}, {"angle": 42}), {"rotate": 42})
        with self.assertRaises(SolutionError):
            normalize_solution("grid", grid_task, {"click": [1, 10]})

    def test_visual_answer_rules_are_enforced(self) -> None:
        text_task = {
            "presentation": {
                "minLength": 2,
                "maxLength": 5,
                "numericMode": 4,
            }
        }
        self.assertEqual(
            normalize_solution("image_text", text_task, {"text": "A1"}),
            {"text": "A1"},
        )
        with self.assertRaises(SolutionError):
            normalize_solution("image_text", text_task, {"text": "ABC"})

        coordinate_task = {"presentation": {"minClicks": 2, "maxClicks": 3}}
        with self.assertRaises(SolutionError):
            normalize_solution(
                "coordinates", coordinate_task, {"coordinates": [{"x": 1, "y": 2}]}
            )

        bounded_grid = {
            "presentation": {"rows": 3, "columns": 3, "minClicks": 2, "maxClicks": 2}
        }
        self.assertEqual(
            normalize_solution("grid", bounded_grid, {"click": [1, 9]}),
            {"click": [1, 9]},
        )
        with self.assertRaises(SolutionError):
            normalize_solution("grid", bounded_grid, {"click": [1]})

    def test_complex_solution_shapes(self) -> None:
        self.assertEqual(
            set(
                normalize_solution(
                    "geetest_v4",
                    {},
                    {
                        "captcha_id": "id",
                        "lot_number": "lot",
                        "pass_token": "pass",
                        "gen_time": "time",
                        "captcha_output": "output",
                    },
                )
            ),
            {"captcha_id", "lot_number", "pass_token", "gen_time", "captcha_output"},
        )
        self.assertEqual(
            normalize_solution("datadome", {}, {"cookie": "datadome=value"}),
            {"cookie": "datadome=value"},
        )


class CompatibilityTranslationTest(unittest.TestCase):
    def test_visual_v2_tasks_are_normalized(self) -> None:
        cases = [
            ("ImageToTextTask", "image_text", {}),
            ("CoordinatesTask", "coordinates", {}),
            ("GridTask", "grid", {"rows": 3, "columns": 3}),
            ("RotateTask", "rotate", {"angle": 5}),
        ]
        for external, internal, extra in cases:
            with self.subTest(external=external):
                translated, returned = translate_create_request(
                    {"task": {"type": external, "body": PNG_1X1, **extra}}
                )
                self.assertEqual(returned, external)
                self.assertEqual(translated["type"], internal)
                self.assertEqual(translated["presentation"]["kind"], internal)

    def test_complex_v2_tasks_are_normalized(self) -> None:
        funcaptcha, _ = translate_create_request(
            {
                "task": {
                    "type": "FunCaptchaTaskProxyless",
                    "websiteURL": "https://example.test",
                    "websitePublicKey": "public-key",
                    "data": {"blob": "blob-value"},
                }
            }
        )
        self.assertEqual(funcaptcha["type"], "funcaptcha")
        self.assertEqual(funcaptcha["websiteKey"], "public-key")

        geetest, _ = translate_create_request(
            {
                "task": {
                    "type": "GeeTestTaskProxyless",
                    "websiteURL": "https://example.test",
                    "version": 4,
                    "initParameters": {"captcha_id": "captcha-id"},
                    "risk_type": "slide",
                }
            }
        )
        self.assertEqual(geetest["type"], "geetest_v4")
        self.assertEqual(geetest["captchaId"], "captcha-id")

        datadome, _ = translate_create_request(
            {
                "task": {
                    "type": "DataDomeSliderTask",
                    "websiteURL": "https://example.test",
                    "captchaUrl": "https://geo.captcha-delivery.com/captcha/?id=test",
                    "userAgent": "Test Browser",
                    "proxyType": "http",
                    "proxyAddress": "127.0.0.1",
                    "proxyPort": 8891,
                }
            }
        )
        self.assertEqual(datadome["type"], "datadome")
        self.assertEqual(datadome["context"]["userAgent"], "Test Browser")
        self.assertEqual(datadome["context"]["proxy"], "http://127.0.0.1:8891")

        amazon_solution = {
            "captcha_voucher": "voucher",
            "existing_token": "existing",
        }
        self.assertEqual(translate_solution("AmazonTask", amazon_solution), amazon_solution)

    def test_amazon_waf_supports_both_official_script_modes(self) -> None:
        jsapi, _ = translate_create_request(
            {
                "task": {
                    "type": "AmazonTaskProxyless",
                    "websiteURL": "https://example.test",
                    "websiteKey": "public-key",
                    "jsapiScript": "https://example.edge.captcha-sdk.awswaf.com/id/jsapi.js",
                }
            }
        )
        public, _, _ = broker.validate_task(jsapi)
        self.assertIn("jsapiScript", public)

        interstitial, _ = translate_create_request(
            {
                "task": {
                    "type": "AmazonTaskProxyless",
                    "websiteURL": "https://example.test",
                    "websiteKey": "public-key",
                    "iv": "fresh-iv",
                    "context": "fresh-context",
                    "challengeScript": "https://id.token.awswaf.com/path/challenge.js",
                    "captchaScript": "https://id.captcha.awswaf.com/path/captcha.js",
                }
            }
        )
        public, _, _ = broker.validate_task(interstitial)
        self.assertEqual(public["awsContext"], "fresh-context")

        incomplete = dict(interstitial)
        incomplete.pop("captchaScript")
        with self.assertRaises(broker.RequestError):
            broker.validate_task(incomplete)

    def test_image_text_numeric_enum_and_click_bounds_are_mapped(self) -> None:
        translated, _ = translate_create_request(
            {
                "task": {
                    "type": "ImageToTextTask",
                    "body": PNG_1X1,
                    "numeric": 4,
                    "minLength": 0,
                    "maxLength": 0,
                }
            }
        )
        presentation = translated["presentation"]
        self.assertEqual(presentation["numericMode"], 4)
        self.assertEqual((presentation["minLength"], presentation["maxLength"]), (1, 1_024))

        coordinates, _ = translate_create_request(
            {
                "task": {
                    "type": "CoordinatesTask",
                    "body": PNG_1X1,
                    "minClicks": 2,
                    "maxClicks": 4,
                }
            }
        )
        self.assertEqual(coordinates["presentation"]["minClicks"], 2)
        self.assertEqual(coordinates["presentation"]["maxClicks"], 4)


class AssetRoundTripTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_asset_is_worker_scoped_and_typed_solution_round_trips(self) -> None:
        created = await self.client.post(
            "/v1/tasks",
            headers={"Authorization": "Bearer phone-secret"},
            json={
                "type": "grid",
                "presentation": {
                    "kind": "grid",
                    "image": {"data": PNG_1X1, "mediaType": "image/png"},
                    "prompt": "选择目标",
                    "rows": 3,
                    "columns": 3,
                },
            },
        )
        self.assertEqual(created.status_code, 201)
        task_id = (await created.get_json())["taskId"]
        joined = await self.client.post(
            "/v1/workers/join",
            headers={"Authorization": "Bearer phone-secret"},
            json={"name": "phone", "domains": [], "types": ["grid"]},
        )
        token = (await joined.get_json())["workerToken"]
        polled = await self.client.post(
            "/v1/workers/poll",
            headers={"Authorization": f"Worker {token}"},
            json={"waitSeconds": 0},
        )
        envelope = await polled.get_json()
        descriptor = envelope["task"]["presentation"]["image"]
        self.assertNotIn("data", descriptor)
        asset_id = descriptor["assetId"]

        unauthorized = await self.client.get(f"/v1/assets/{asset_id}")
        self.assertEqual(unauthorized.status_code, 401)
        asset = await self.client.get(
            f"/v1/assets/{asset_id}", headers={"Authorization": f"Worker {token}"}
        )
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.content_type, "image/png")

        submitted = await self.client.post(
            "/v1/workers/submit",
            headers={"Authorization": f"Worker {token}"},
            json={"taskId": task_id, "status": "ready", "solution": {"cells": [1, 5]}},
        )
        self.assertEqual(submitted.status_code, 200)
        result = await self.client.get(
            f"/v1/tasks/{task_id}", headers={"Authorization": "Bearer phone-secret"}
        )
        self.assertEqual((await result.get_json())["solution"], {"click": [1, 5]})
        expired_asset = await self.client.get(
            f"/v1/assets/{asset_id}", headers={"Authorization": f"Worker {token}"}
        )
        self.assertEqual(expired_asset.status_code, 404)

    async def test_datadome_requires_bound_network_context(self) -> None:
        response = await self.client.post(
            "/v1/tasks",
            headers={"Authorization": "Bearer phone-secret"},
            json={
                "type": "datadome",
                "websiteURL": "https://example.test",
                "captchaUrl": "https://geo.captcha-delivery.com/captcha/?id=test",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await response.get_json())["errorCode"], "ERROR_BAD_CONTEXT")

    async def test_twocaptcha_image_task_round_trips_text_solution(self) -> None:
        timestamp = time.time()
        with broker.db() as connection:
            connection.execute(
                "INSERT INTO runs"
                " (id,registration_id,registration_name,status,started_at,updated_at)"
                " VALUES (?,?,?,'running',?,?)",
                ("run-image", "demo", "Demo", timestamp, timestamp),
            )
        created = await self.client.post(
            "/createTask",
            json={
                "clientKey": "phone-secret",
                "task": {
                    "type": "ImageToTextTask",
                    "body": PNG_1X1,
                    "comment": "输入字符",
                    "minLength": 2,
                    "maxLength": 5,
                },
            },
        )
        compatibility_id = (await created.get_json())["taskId"]
        joined = await self.client.post(
            "/v1/workers/join",
            headers={"Authorization": "Bearer phone-secret"},
            json={"name": "image-phone", "domains": [], "types": ["image_text"]},
        )
        token = (await joined.get_json())["workerToken"]
        polled = await self.client.post(
            "/v1/workers/poll",
            headers={"Authorization": f"Worker {token}"},
            json={"runId": "run-image", "waitSeconds": 0},
        )
        task_id = (await polled.get_json())["taskId"]
        submitted = await self.client.post(
            "/v1/workers/submit",
            headers={"Authorization": f"Worker {token}"},
            json={"taskId": task_id, "status": "ready", "solution": {"text": "AB12"}},
        )
        self.assertEqual(submitted.status_code, 200)
        result = await self.client.post(
            "/getTaskResult",
            json={"clientKey": "phone-secret", "taskId": compatibility_id},
        )
        payload = await result.get_json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["solution"], {"text": "AB12"})


if __name__ == "__main__":
    unittest.main()
