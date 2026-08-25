from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "app-src/app/src/main/java/app/captchamesh/MainActivity.java"
WATCH_SERVICE = ROOT / "app-src/app/src/main/java/app/captchamesh/CaptchaWatchService.java"
RELAY_SERVICE = ROOT / "app-src/app/src/main/java/app/captchamesh/RelayWatchService.java"
NOTIFICATION_PREFERENCES = ROOT / "app-src/app/src/main/java/app/captchamesh/NotificationPreferences.java"
MANIFEST = ROOT / "app-src/app/src/main/AndroidManifest.xml"
BUILD = ROOT / "app-src/app/build.gradle.kts"
LAUNCHER = ROOT / "app-src/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml"
LAUNCHER_FOREGROUND = ROOT / "app-src/app/src/main/res/drawable/ic_launcher_fg.xml"
LAUNCHER_ART = ROOT / "app-src/app/src/main/res/drawable-nodpi/ic_launcher_art.png"


class AndroidUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activity = ACTIVITY.read_text(encoding="utf-8")
        cls.watch_service = WATCH_SERVICE.read_text(encoding="utf-8")
        cls.relay_service = RELAY_SERVICE.read_text(encoding="utf-8")
        cls.notification_preferences = NOTIFICATION_PREFERENCES.read_text(encoding="utf-8")
        cls.manifest = MANIFEST.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")

    def test_compact_navigation_keeps_android_touch_target(self):
        self.assertIn("return dp(isLandscape() ? 56 : 64);", self.activity)
        self.assertIn("item.setMinimumHeight(dp(48));", self.activity)
        self.assertIn("navigationIndicators", self.activity)

    def test_task_page_exposes_supported_manual_challenge_families(self):
        for label in (
            "图片文字",
            "坐标点击",
            "图片网格",
            "旋转校正",
            "网页验证",
            "FunCaptcha",
            "GeeTest v3/v4",
            "DataDome",
            "Amazon WAF",
        ):
            self.assertIn(f'"{label}"', self.activity)
        self.assertIn('registration.optJSONArray("captchaTypes")', self.activity)

    def test_local_log_clear_does_not_remove_api_key(self):
        method = re.search(
            r"private void clearLocalLog\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group("body")
        self.assertIn("LOCAL_RECORDS_CIPHERTEXT", body)
        self.assertIn("LOCAL_RECORDS_IV", body)
        self.assertNotIn("API_KEY_CIPHERTEXT", body)
        self.assertNotIn("API_KEY_IV", body)

    def test_user_started_run_waits_in_background_and_notifies(self):
        self.assertIn("android.permission.POST_NOTIFICATIONS", self.manifest)
        self.assertIn("android.permission.FOREGROUND_SERVICE_DATA_SYNC", self.manifest)
        self.assertIn('android:foregroundServiceType="dataSync"', self.manifest)
        self.assertIn("startForeground(WAITING_NOTIFICATION_ID", self.watch_service)
        self.assertIn('tasks.optInt("pending", 0)', self.watch_service)
        self.assertIn('tasks.optInt("leased", 0)', self.watch_service)
        self.assertIn("NotificationCompat.PRIORITY_HIGH", self.watch_service)
        self.assertIn("ACTION_OPEN_CHALLENGE", self.watch_service)
        self.assertIn('"立即处理"', self.watch_service)
        self.assertIn('"停止任务"', self.watch_service)
        self.assertIn(' + "/stop"', self.watch_service)
        self.assertIn("PendingIntent.getForegroundService", self.watch_service)
        self.assertIn("if (!foregroundVisible)", self.activity)
        self.assertIn("CaptchaWatchService.start(this, activeRunId, name)", self.activity)
        self.assertIn('.put("retryable", true)', self.activity)

    def test_diagnostics_and_timeline_are_explicit_and_readable(self):
        self.assertIn("R.id.nav_diagnostics", self.activity)
        self.assertIn('pageHeading("自检"', self.activity)
        self.assertIn('"Broker 健康"', self.activity)
        self.assertIn('"API 鉴权"', self.activity)
        self.assertIn('"通知与后台"', self.activity)
        self.assertIn('text("任务时间线"', self.activity)
        for status in ("完成", "进行中", "等待", "未执行"):
            self.assertIn(f'"{status}"', self.activity)

    def test_agent_tasks_are_default_and_workflows_are_optional(self):
        self.assertIn("private int selectedPageId = R.id.nav_task;", self.activity)
        self.assertIn('R.id.nav_registrations, R.drawable.ic_list, "工作流"', self.activity)
        self.assertIn('pageHeading("Agent 任务"', self.activity)
        self.assertIn("电脑通过本机 API 自动发送", self.activity)
        self.assertIn("可选：从手机启动电脑端白名单脚本", self.activity)
        self.assertNotIn('"注册机"', self.activity)

    def test_task_alert_setting_controls_both_modes(self):
        self.assertIn("CAPTCHA 到达时显示提醒", self.activity)
        self.assertIn("ACTION_APP_NOTIFICATION_SETTINGS", self.activity)
        self.assertIn("taskAlertsEnabled", self.watch_service)
        self.assertIn("taskAlertsEnabled", self.relay_service)
        self.assertIn('TASK_ALERTS = "notify_task_alerts"', self.notification_preferences)
        self.assertIn("manager.cancel(WORKFLOW_CHALLENGE_ID)", self.notification_preferences)
        self.assertIn("manager.cancel(AGENT_CHALLENGE_ID)", self.notification_preferences)

    def test_status_lists_use_flat_rows_instead_of_nested_cards(self):
        for method_name in ("timelineRow", "diagnosticRow"):
            method = re.search(
                rf"private LinearLayout {method_name}\(.*?\) \{{(?P<body>.*?)\n    \}}",
                self.activity,
                re.DOTALL,
            )
            self.assertIsNotNone(method)
            self.assertNotIn("setBackground", method.group("body"))
        self.assertNotIn("buildTimelineCard()", self.activity)
        self.assertIn("sectionDivider(dp(44))", self.activity)

    def test_release_version_is_0190(self):
        self.assertIn("versionCode = 22", self.build)
        self.assertIn('versionName = "0.19.0"', self.build)

    def test_launcher_icon_uses_safe_artwork_and_themed_monochrome_mark(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        foreground = LAUNCHER_FOREGROUND.read_text(encoding="utf-8")
        self.assertIn('@drawable/ic_launcher_art', launcher)
        self.assertIn('@drawable/ic_launcher_fg', launcher)
        self.assertNotIn('M0,0h108v108', foreground)
        self.assertIn('M54,31 L74,41', foreground)
        self.assertTrue(LAUNCHER_ART.is_file())
        self.assertGreater(LAUNCHER_ART.stat().st_size, 10_000)
        self.assertIn('android:roundIcon="@mipmap/ic_launcher"', self.manifest)

    def test_e2ee_pairing_uses_keystore_and_generic_background_notifications(self):
        relay_store = (ACTIVITY.parent / "RelayStore.java").read_text(encoding="utf-8")
        relay_service = (ACTIVITY.parent / "RelayWatchService.java").read_text(encoding="utf-8")
        self.assertIn('android:scheme="captchamesh"', self.manifest)
        self.assertIn('foregroundServiceType="remoteMessaging"', self.manifest)
        self.assertIn("AndroidKeyStore", relay_store)
        self.assertIn("RelayCrypto.decrypt", self.activity)
        self.assertIn("个人 Agent 需要你验证", relay_service)
        self.assertNotIn("websiteUrl", relay_service)

    def test_official_broker_domain_migrates_debug_value_once(self):
        self.assertIn('DEFAULT_BROKER = "https://mesh.vimalinx.com"', self.activity)
        self.assertIn('BROKER_DOMAIN_MIGRATION', self.activity)
        self.assertIn('"http://127.0.0.1:8890".equals(savedBroker)', self.activity)
        self.assertIn('"https://mesh.vimalinx.com"', self.watch_service)


if __name__ == "__main__":
    unittest.main()
