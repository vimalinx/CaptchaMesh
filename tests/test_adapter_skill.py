from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = PROJECT_ROOT / ".skill/captchamesh-adapter/scripts/inspect_registration.py"


class AdapterSkillInspectorTest(unittest.TestCase):
    def run_inspector(
        self, source: str, mode: str = "agent-api"
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "register.py"
            fixture.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(INSPECTOR), str(fixture), "--mode", mode, "--json"],
                check=False,
                capture_output=True,
                text=True,
            )
        return result, json.loads(result.stdout)

    def test_official_python_sdk_is_classified_without_echoing_secrets(self) -> None:
        secret = "private-test-key-that-must-not-appear"
        result, report = self.run_inspector(
            "from twocaptcha import TwoCaptcha\n"
            f"solver = TwoCaptcha('{secret}', server='2captcha.com')\n"
            "solver.turnstile(sitekey='site', url='https://example.test')\n"
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("2captcha_v1", report["protocols"])
        self.assertIn("turnstile", report["supported_task_signals"])
        self.assertEqual(report["integration_mode"], "agent-api")
        self.assertTrue(any("127.0.0.1:8893" in action for action in report["recommended_actions"]))
        self.assertTrue(any("without starting" in action for action in report["recommended_actions"]))
        self.assertTrue(report["ready_for_supported_adapter"])
        self.assertNotIn(secret, result.stdout)

    def test_v2_funcaptcha_capability_is_ready(self) -> None:
        result, report = self.run_inspector(
            "base = 'https://api.2captcha.com'\n"
            "endpoint = base + '/createTask'\n"
            "task = {'type': 'FunCaptchaTaskProxyless'}\n"
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("2captcha_v2", report["protocols"])
        self.assertIn("funcaptcha", report["supported_task_signals"])
        self.assertTrue(report["ready_for_supported_adapter"])

    def test_callback_still_blocks_ready_status(self) -> None:
        result, report = self.run_inspector(
            "endpoint = 'https://api.2captcha.com/createTask'\n"
            "payload = {'callbackUrl': 'https://example.test/hook'}\n"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("callback_or_pingback", report["unsupported_capability_signals"])
        self.assertFalse(report["ready_for_supported_adapter"])

    def test_phone_workflow_mode_requires_injected_run_context(self) -> None:
        result, report = self.run_inspector(
            "endpoint = base + '/createTask'\n"
            "task = {'type': 'TurnstileTaskProxyless'}\n",
            mode="phone-workflow",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["integration_mode"], "phone-workflow")
        self.assertTrue(any("injected" in action for action in report["recommended_actions"]))
        self.assertTrue(any("Workflows page" in action for action in report["recommended_actions"]))


if __name__ == "__main__":
    unittest.main()
