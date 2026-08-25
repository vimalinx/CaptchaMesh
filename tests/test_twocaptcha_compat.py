from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import broker
from twocaptcha_compat import (
    TwoCaptchaCompatError,
    translate_create_request,
    translate_v1_request,
)


class TranslationTest(unittest.TestCase):
    def test_turnstile_challenge_fields_are_mapped(self) -> None:
        translated, external_type = translate_create_request(
            {
                "clientKey": "local",
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": "https://example.test/register",
                    "websiteKey": "site-key",
                    "action": "managed",
                    "data": "challenge-data",
                    "pagedata": "page-data",
                    "userAgent": "Test Browser",
                },
            }
        )
        self.assertEqual(external_type, "TurnstileTaskProxyless")
        self.assertEqual(translated["type"], "turnstile")
        self.assertEqual(translated["cData"], "challenge-data")
        self.assertEqual(translated["chlPageData"], "page-data")
        self.assertEqual(translated["context"]["userAgent"], "Test Browser")

    def test_recaptcha_cookies_and_proxy_are_mapped(self) -> None:
        translated, _ = translate_create_request(
            {
                "task": {
                    "type": "RecaptchaV2Task",
                    "websiteURL": "https://example.test/register",
                    "websiteKey": "site-key",
                    "cookies": "session=abc=123; flow=ready",
                    "proxyType": "http",
                    "proxyAddress": "127.0.0.1",
                    "proxyPort": "8891",
                }
            }
        )
        self.assertEqual(translated["context"]["proxy"], "http://127.0.0.1:8891")
        self.assertEqual(
            translated["context"]["cookies"],
            [
                {"name": "session", "value": "abc=123", "path": "/"},
                {"name": "flow", "value": "ready", "path": "/"},
            ],
        )

    def test_unsupported_features_fail_before_a_phone_task_is_created(self) -> None:
        with self.assertRaisesRegex(TwoCaptchaCompatError, "callbackUrl"):
            translate_create_request(
                {
                    "callbackUrl": "https://example.test/callback",
                    "task": {"type": "TurnstileTaskProxyless"},
                }
            )

    def test_v1_recaptcha_v3_and_legacy_cookies_are_mapped(self) -> None:
        translated, external_type = translate_v1_request(
            {
                "method": "userrecaptcha",
                "version": "v3",
                "googlekey": "site-key",
                "pageurl": "https://example.test/register",
                "action": "signup",
                "cookies": "session:abc; flow:ready",
            }
        )
        self.assertEqual(external_type, "RecaptchaV3TaskProxyless")
        self.assertEqual(translated["type"], "recaptcha_v3")
        self.assertEqual(translated["action"], "signup")
        self.assertEqual(translated["context"]["cookies"][0]["value"], "abc")

    def test_v1_proxy_and_unsupported_methods_are_explicit(self) -> None:
        translated, external_type = translate_v1_request(
            {
                "method": "turnstile",
                "sitekey": "site-key",
                "pageurl": "https://example.test/register",
                "proxy": "127.0.0.1:8891",
                "proxytype": "HTTP",
            }
        )
        self.assertEqual(external_type, "TurnstileTask")
        self.assertEqual(translated["context"]["proxy"], "http://127.0.0.1:8891")
        with self.assertRaisesRegex(TwoCaptchaCompatError, "unsupported 2Captcha v1 method"):
            translate_v1_request(
                {
                    "method": "base64",
                    "sitekey": "unused",
                    "pageurl": "https://example.test/register",
                }
            )
        with self.assertRaisesRegex(TwoCaptchaCompatError, "authenticated proxies"):
            translate_create_request(
                {
                    "task": {
                        "type": "HCaptchaTask",
                        "websiteURL": "https://example.test",
                        "websiteKey": "site-key",
                        "proxyType": "http",
                        "proxyAddress": "127.0.0.1",
                        "proxyPort": 8080,
                        "proxyLogin": "user",
                    }
                }
            )


class TwoCaptchaApiTest(unittest.IsolatedAsyncioTestCase):
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

    def start_run(self, run_id: str = "run-test", registration_id: str = "demo") -> None:
        timestamp = time.time()
        with broker.db() as connection:
            connection.execute(
                "INSERT INTO runs"
                " (id,registration_id,registration_name,status,started_at,updated_at)"
                " VALUES (?,?,?,'running',?,?)",
                (run_id, registration_id, "Demo Registration", timestamp, timestamp),
            )

    async def create_recaptcha_task(self) -> int:
        response = await self.client.post(
            "/createTask",
            json={
                "clientKey": "phone-secret",
                "task": {
                    "type": "RecaptchaV2TaskProxyless",
                    "websiteURL": "https://example.test/register",
                    "websiteKey": "site-key",
                    "isInvisible": False,
                },
            },
        )
        payload = await response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["errorId"], 0)
        self.assertIsInstance(payload["taskId"], int)
        return payload["taskId"]

    async def create_v1_turnstile_task(self, *, json_mode: bool = False) -> int:
        response = await self.client.post(
            "/in.php",
            form={
                "key": "phone-secret",
                "method": "turnstile",
                "sitekey": "site-key",
                "pageurl": "https://example.test/register",
                "json": "1" if json_mode else "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        if json_mode:
            payload = await response.get_json()
            self.assertEqual(payload["status"], 1)
            return int(payload["request"])
        return int((await response.get_data(as_text=True)).removeprefix("OK|"))

    async def test_create_infers_the_single_active_registration_run(self) -> None:
        self.start_run()
        compatibility_id = await self.create_recaptcha_task()
        with broker.db() as connection:
            row = connection.execute(
                "SELECT t.*,c.external_type FROM twocaptcha_tasks c"
                " JOIN tasks t ON t.id=c.internal_task_id WHERE c.id=?",
                (compatibility_id,),
            ).fetchone()
        self.assertEqual(row["run_id"], "run-test")
        self.assertEqual(row["type"], "recaptcha_v2")
        self.assertEqual(row["external_type"], "RecaptchaV2TaskProxyless")
        stored = json.loads(row["task_json"])
        self.assertEqual(stored["registrationId"], "demo")

    async def test_standard_worker_round_trip_returns_2captcha_solution_shape(self) -> None:
        self.start_run()
        compatibility_id = await self.create_recaptcha_task()
        joined = await self.client.post(
            "/v1/workers/join",
            headers={"Authorization": "Bearer phone-secret"},
            json={
                "name": "manual-test-phone",
                "domains": [],
                "types": ["recaptcha_v2"],
                "appVersion": "test",
                "device": "test",
            },
        )
        worker_token = (await joined.get_json())["workerToken"]
        polled = await self.client.post(
            "/v1/workers/poll",
            headers={"Authorization": f"Worker {worker_token}"},
            json={"runId": "run-test", "waitSeconds": 0},
        )
        task = await polled.get_json()
        token = "manual-solution-token-1234567890"
        submitted = await self.client.post(
            "/v1/workers/submit",
            headers={"Authorization": f"Worker {worker_token}"},
            json={"taskId": task["taskId"], "status": "ready", "solution": {"token": token}},
        )
        self.assertEqual(submitted.status_code, 200)

        result = await self.client.post(
            "/getTaskResult",
            json={"clientKey": "phone-secret", "taskId": str(compatibility_id)},
        )
        payload = await result.get_json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["solution"]["gRecaptchaResponse"], token)
        self.assertEqual(payload["solution"]["token"], token)
        self.assertEqual(payload["cost"], "0.00000")
        self.assertEqual(payload["solveCount"], 1)

        report = await self.client.post(
            "/reportCorrect",
            json={"clientKey": "phone-secret", "taskId": compatibility_id},
        )
        self.assertEqual(await report.get_json(), {"errorId": 0, "status": "success"})
        with broker.db() as connection:
            feedback = connection.execute(
                "SELECT feedback FROM twocaptcha_tasks WHERE id=?", (compatibility_id,)
            ).fetchone()[0]
        self.assertEqual(feedback, "correct")

    async def test_auth_and_run_errors_use_2captcha_http_200_shape(self) -> None:
        wrong_key = await self.client.post(
            "/createTask",
            json={"clientKey": "wrong", "task": {"type": "TurnstileTaskProxyless"}},
        )
        self.assertEqual(wrong_key.status_code, 200)
        self.assertEqual((await wrong_key.get_json())["errorCode"], "ERROR_KEY_DOES_NOT_EXIST")

        no_run = await self.client.post(
            "/createTask",
            json={
                "clientKey": "phone-secret",
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": "https://example.test",
                    "websiteKey": "site-key",
                },
            },
        )
        self.assertEqual(no_run.status_code, 200)
        self.assertEqual((await no_run.get_json())["errorCode"], "ERROR_NO_ACTIVE_RUN")

        self.start_run("run-first", "first")
        self.start_run("run-second", "second")
        ambiguous = await self.client.post(
            "/createTask",
            json={
                "clientKey": "phone-secret",
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": "https://example.test",
                    "websiteKey": "site-key",
                },
            },
        )
        self.assertEqual((await ambiguous.get_json())["errorCode"], "ERROR_AMBIGUOUS_RUN")

    async def test_balance_is_compatible_with_clients_that_preflight_funds(self) -> None:
        response = await self.client.post(
            "/getBalance", json={"clientKey": "phone-secret"}
        )
        self.assertEqual(
            await response.get_json(), {"errorId": 0, "balance": "999999.00000"}
        )

    async def test_v1_plain_and_json_submission_and_pending_result(self) -> None:
        self.start_run()
        plain_id = await self.create_v1_turnstile_task()
        pending = await self.client.get(
            "/res.php", query_string={"key": "phone-secret", "action": "get", "id": plain_id}
        )
        self.assertEqual(await pending.get_data(as_text=True), "CAPCHA_NOT_READY")

        with broker.db() as connection:
            connection.execute("UPDATE runs SET status='running' WHERE id='run-test'")
        json_id = await self.create_v1_turnstile_task(json_mode=True)
        pending_json = await self.client.get(
            "/res.php",
            query_string={
                "key": "phone-secret",
                "action": "get",
                "id": json_id,
                "json": 1,
            },
        )
        self.assertEqual(
            await pending_json.get_json(), {"status": 0, "request": "CAPCHA_NOT_READY"}
        )

    async def test_v1_solved_get2_balance_feedback_and_cors(self) -> None:
        self.start_run()
        compatibility_id = await self.create_v1_turnstile_task()
        token = "turnstile-token-from-phone"
        with broker.db() as connection:
            internal_id = connection.execute(
                "SELECT internal_task_id FROM twocaptcha_tasks WHERE id=?",
                (compatibility_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE tasks SET status='solved',result=?,updated_at=? WHERE id=?",
                (json.dumps({"token": token}), time.time(), internal_id),
            )

        result = await self.client.get(
            "/res.php",
            query_string={
                "key": "phone-secret",
                "action": "get2",
                "id": compatibility_id,
                "json": 1,
                "header_acao": 1,
            },
        )
        self.assertEqual(
            await result.get_json(),
            {"status": 1, "request": token, "price": "0.00000"},
        )
        self.assertEqual(result.headers["Access-Control-Allow-Origin"], "*")

        balance = await self.client.get(
            "/res.php",
            query_string={"key": "phone-secret", "action": "getbalance"},
        )
        self.assertEqual(await balance.get_data(as_text=True), "999999.00000")

        report = await self.client.get(
            "/res.php",
            query_string={"key": "phone-secret", "action": "reportgood", "id": compatibility_id},
        )
        self.assertEqual(await report.get_data(as_text=True), "OK_REPORT_RECORDED")
        duplicate = await self.client.get(
            "/res.php",
            query_string={"key": "phone-secret", "action": "reportbad", "id": compatibility_id},
        )
        self.assertEqual(await duplicate.get_data(as_text=True), "ERROR_DUPLICATE_REPORT")

    async def test_v1_auth_bad_id_and_unsupported_submission_errors(self) -> None:
        wrong_key = await self.client.get(
            "/res.php", query_string={"key": "wrong", "action": "getbalance", "json": 1}
        )
        self.assertEqual(
            await wrong_key.get_json(), {"status": 0, "request": "ERROR_KEY_DOES_NOT_EXIST"}
        )
        bad_id = await self.client.get(
            "/res.php",
            query_string={"key": "phone-secret", "action": "get", "id": "abc"},
        )
        self.assertEqual(await bad_id.get_data(as_text=True), "ERROR_WRONG_ID_FORMAT")
        unsupported = await self.client.post(
            "/in.php",
            form={
                "key": "phone-secret",
                "method": "base64",
                "body": "not-enqueued",
                "pageurl": "https://example.test/register",
            },
        )
        self.assertEqual(await unsupported.get_data(as_text=True), "ERROR_BAD_PARAMETERS")


if __name__ == "__main__":
    unittest.main()
