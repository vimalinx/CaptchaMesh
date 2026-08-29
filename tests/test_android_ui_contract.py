from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "app-src/app/src/main/java/app/captchamesh/MainActivity.java"
WATCH_SERVICE = ROOT / "app-src/app/src/main/java/app/captchamesh/CaptchaWatchService.java"
RELAY_SERVICE = ROOT / "app-src/app/src/main/java/app/captchamesh/RelayWatchService.java"
DIAGNOSTIC_LOG = ROOT / "app-src/app/src/main/java/app/captchamesh/DiagnosticLog.java"
APPLICATION = ROOT / "app-src/app/src/main/java/app/captchamesh/CaptchaMeshApp.java"
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

    def test_task_page_is_action_first_instead_of_capability_marketing(self):
        self.assertIn('text("当前任务"', self.activity)
        for action in ("继续接题", "重跑该工作流", "暂停接题", "停止工作流", "刷新"):
            self.assertIn(f'"{action}"', self.activity)
        self.assertNotIn('"再来一个"', self.activity)
        self.assertNotIn('text("任务时间线"', self.activity)
        self.assertNotIn('text("接题能力"', self.activity)
        self.assertNotIn('"14 类人工挑战 · 按任务自动选择界面"', self.activity)
        self.assertIn('registration.optJSONArray("captchaTypes")', self.activity)

    def test_task_progress_is_live_compact_and_collapsed_by_default(self):
        self.assertIn("new ProgressBar(this, null, android.R.attr.progressBarStyleSmall)", self.activity)
        self.assertIn("progressSpinner.setIndeterminate(true)", self.activity)
        self.assertIn("new FrameLayout.LayoutParams(dp(20), dp(20))", self.activity)
        self.assertIn("progressHistoryList.setVisibility(View.GONE)", self.activity)
        self.assertIn("progressHistoryEntries.add(0, currentProgressEntry)", self.activity)
        self.assertIn('!"等待新任务".equals(currentProgressEntry)', self.activity)
        self.assertIn("while (progressHistoryEntries.size() > 4)", self.activity)
        self.assertIn("ValueAnimator.areAnimatorsEnabled()", self.activity)
        self.assertIn("publishProgress(state, value, foreground)", self.activity)
        self.assertIn("taskStateIsActive(state)", self.activity)
        self.assertNotIn("progressIsActive(String badge)", self.activity)
        self.assertIn("progressCurrent.setAccessibilityLiveRegion", self.activity)
        self.assertIn('"展开"', self.activity)
        self.assertIn('"收起"', self.activity)
        for obsolete_step in (
            "电脑提交任务",
            "加密发送到手机",
            "等待人工处理",
            "验证结果回传",
            "Agent 继续运行",
        ):
            self.assertNotIn(f'"{obsolete_step}"', self.activity)

    def test_task_actions_have_real_state_transitions(self):
        self.assertIn("private void continueTaskIntake()", self.activity)
        self.assertIn("private void rerunLastWorkflow()", self.activity)
        self.assertIn("private void pauseTaskIntake()", self.activity)
        self.assertIn("private void refreshCurrentTask()", self.activity)
        self.assertIn("private void confirmStopCurrentWorkflow()", self.activity)
        self.assertIn("private void stopCurrentWorkflow(String runId)", self.activity)
        self.assertIn(".putBoolean(TASK_INTAKE_PAUSED, true)", self.activity)
        self.assertIn("RelayWatchService.stop(this)", self.activity)
        self.assertIn("static void stop(Context context)", self.relay_service)
        self.assertIn("state == TaskPanelState.RESULT_SENT", self.activity)
        self.assertIn("startRegistration(lastRegistrationId", self.activity)
        self.assertIn('android.net.Uri.encode(runId) + "/stop"', self.activity)
        self.assertGreaterEqual(
            self.activity.count("new LinearLayout.LayoutParams(0, dp(48), 1)"),
            5,
        )
        pause = re.search(
            r"private void pauseTaskIntake\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(pause)
        self.assertNotIn('"/stop"', pause.group("body"))

    def test_task_panel_uses_one_typed_state_and_mutually_exclusive_actions(self):
        for state in (
            "DISCONNECTED", "IDLE", "PAUSED", "STARTING_WORKFLOW",
            "WORKFLOW_RUNNING", "RELAY_LOADING", "WAITING_HUMAN",
            "REFRESHING", "STOPPING_WORKFLOW", "COMPLETED", "FAILED",
        ):
            self.assertIn(state, self.activity)
        controls = re.search(
            r"private void updateTaskControls\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(controls)
        body = controls.group("body")
        self.assertIn("switch (taskPanelState)", body)
        self.assertIn("hideTaskAction(continueTaskButton)", body)
        self.assertIn('showTaskAction(pauseTaskButton, "暂停接题")', body)
        self.assertIn('showTaskAction(continueTaskButton, "继续接题")', body)
        self.assertIn('showTaskAction(stopWorkflowButton, "停止工作流")', body)
        self.assertNotIn("relayProcessing", body)

    def test_refresh_is_read_only_and_terminal_status_is_reconciled(self):
        refresh = re.search(
            r"private void refreshCurrentTask\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(refresh)
        body = refresh.group("body")
        self.assertIn("Http.get(", body)
        self.assertIn("reconcileTerminalRun(runId)", body)
        self.assertNotIn("processPendingRelay()", body)
        self.assertNotIn("RelayWatchService.start", body)
        self.assertNotIn("Http.post(", body)

    def test_workflow_and_agent_processing_share_only_a_fair_human_focus_lock(self):
        self.assertIn("workflowExecutor.submit(() -> runRegistration", self.activity)
        self.assertIn("workflowExecutor.submit(() -> finishRegistrationMonitor", self.activity)
        self.assertIn("relayExecutor.submit(() -> solveRelayEnvelope", self.activity)
        relay = re.search(
            r"private void processPendingRelay\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(relay)
        self.assertLess(
            relay.group("body").index("relayProcessing = true"),
            relay.group("body").index("relayExecutor.submit"),
        )
        self.assertNotIn("active || !storedRunId().isEmpty()", relay.group("body"))
        self.assertIn("challengeVisible()", relay.group("body"))
        self.assertIn("humanChallengeLock.lockInterruptibly()", self.activity)
        self.assertIn("humanChallengeLock.unlock()", self.activity)

        start = re.search(
            r"private void startRegistration\(String registrationId, String name\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(start)
        self.assertIn("challengeVisible()", start.group("body"))
        self.assertNotIn("relayProcessing", start.group("body"))
        self.assertNotIn("RelayStore.PREF_PENDING", start.group("body"))

    def test_agent_queue_is_durable_multi_task_and_visible(self):
        relay_store = (ACTIVITY.parent / "RelayStore.java").read_text(encoding="utf-8")
        self.assertIn('PREF_PENDING_QUEUE = "relay_pending_envelopes_v2"', relay_store)
        self.assertIn("MAX_PENDING_ENVELOPES = 32", relay_store)
        self.assertIn("static synchronized EnqueueResult enqueueEnvelope", relay_store)
        self.assertIn(".commit()", relay_store)
        self.assertIn("static synchronized JSONObject peekEnvelope", relay_store)
        self.assertIn("static synchronized boolean removeEnvelope", relay_store)
        self.assertIn('text("并发任务"', self.activity)
        self.assertIn('current ? "处理中" : "等待中"', self.activity)
        self.assertIn("agentTaskSummary.setAccessibilityLiveRegion", self.activity)
        self.assertIn("RelayStore.pendingEnvelopes(this)", self.activity)
        self.assertIn("ACTION_QUEUE_CHANGED", self.relay_service)
        self.assertIn("MainActivity.notifyRelayQueueChanged()", self.relay_service)
        self.assertIn("relayUiHandler.postDelayed(this, 1000)", self.activity)
        self.assertIn("pending != observedRelayCount", self.activity)
        self.assertIn("relayUiHandler.removeCallbacks(relayQueuePulse)", self.activity)
        self.assertIn("advanceRelayAfterChallenge = true", self.activity)
        self.assertIn("if (advanceRelayAfterChallenge)", self.activity)
        self.assertIn("challengeCard.post(this::processPendingRelay)", self.activity)
        enqueue = self.relay_service.index("RelayStore.enqueueEnvelope")
        ack = self.relay_service.index('config.hub + "/v1/relay/ack"')
        self.assertLess(enqueue, ack)
        self.assertNotIn("PREF_PENDING, \"\").isEmpty()", self.relay_service)

    def test_current_task_identifies_the_actual_challenge(self):
        show = re.search(
            r"public void showChallenge\(View challenge, CaptchaTask task\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(show)
        self.assertIn("setTaskState(TaskPanelState.WAITING_HUMAN", show.group("body"))
        self.assertIn('friendlyCaptcha(task.type) + " · " + task.host()', show.group("body"))
        self.assertIn('runState.setContentDescription("当前任务，"', self.activity)

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
        self.assertIn("DiagnosticLog.clear(this)", body)
        self.assertNotIn("API_KEY_CIPHERTEXT", body)
        self.assertNotIn("API_KEY_IV", body)

    def test_user_started_run_waits_in_background_and_notifies(self):
        self.assertIn("android.permission.POST_NOTIFICATIONS", self.manifest)
        self.assertIn("android.permission.FOREGROUND_SERVICE_DATA_SYNC", self.manifest)
        self.assertIn("android.permission.WAKE_LOCK", self.manifest)
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

    def test_agent_relay_holds_only_a_bounded_releasable_wake_lock(self):
        self.assertIn("PowerManager.PARTIAL_WAKE_LOCK", self.relay_service)
        self.assertIn("android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", self.manifest)
        self.assertIn("WAKE_LOCK_TIMEOUT_MS", self.relay_service)
        self.assertIn("WAKE_LOCK_REFRESH_MS", self.relay_service)
        self.assertIn("wakeLock.acquire(WAKE_LOCK_TIMEOUT_MS)", self.relay_service)
        self.assertIn("private void releaseWakeLock()", self.relay_service)
        destroy = re.search(
            r"public void onDestroy\(\) \{(?P<body>.*?)\n    \}",
            self.relay_service,
            re.DOTALL,
        )
        self.assertIsNotNone(destroy)
        self.assertIn("releaseWakeLock()", destroy.group("body"))

    def test_settings_explain_and_link_background_protection(self):
        self.assertIn('"开启后台保护"', self.activity)
        self.assertIn("ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", self.activity)
        self.assertIn("ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS", self.activity)
        self.assertIn("refreshBatteryProtectionStatus()", self.activity)
        self.assertIn('"后台保护未开启；荣耀等系统退到后台后可能冻结连接"', self.activity)
        self.assertIn("backgroundProtectionButton.setEnabled(false)", self.activity)
        self.assertIn("backgroundStatusText()", self.relay_service)
        self.assertIn('"HONOR".equalsIgnoreCase(Build.MANUFACTURER)', self.activity)
        self.assertIn('"打开荣耀管家"', self.activity)
        self.assertIn("getLaunchIntentForPackage", self.activity)
        self.assertIn('"com.hihonor.systemmanager"', self.activity)
        self.assertIn("SecurityException | android.content.ActivityNotFoundException", self.activity)
        self.assertIn("关闭自动管理并允许三项后台活动", self.activity)

    def test_diagnostics_remain_explicit_while_task_steps_are_removed(self):
        self.assertIn("R.id.nav_diagnostics", self.activity)
        self.assertIn('pageHeading("自检"', self.activity)
        self.assertIn('"Broker 健康"', self.activity)
        self.assertIn('"API 鉴权"', self.activity)
        self.assertIn('"通知与后台"', self.activity)
        self.assertNotIn("timelineRow", self.activity)

    def test_agent_tasks_are_default_and_workflows_are_optional(self):
        self.assertIn("private int selectedPageId = R.id.nav_task;", self.activity)
        self.assertIn('R.id.nav_registrations, R.drawable.ic_list, "工作流"', self.activity)
        self.assertIn('pageHeading("Agent 任务"', self.activity)
        self.assertIn("当前任务与操作", self.activity)
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
        method = re.search(
            r"private LinearLayout diagnosticRow\(.*?\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        self.assertNotIn("setBackground", method.group("body"))
        self.assertNotIn("timelineRow", self.activity)
        self.assertNotIn("buildTimelineCard()", self.activity)
        self.assertIn("sectionDivider(dp(44))", self.activity)

    def test_diagnostic_log_is_encrypted_bounded_and_explicitly_redacted(self):
        diagnostic = DIAGNOSTIC_LOG.read_text(encoding="utf-8")
        application = APPLICATION.read_text(encoding="utf-8")
        self.assertIn("AES/GCM/NoPadding", diagnostic)
        self.assertIn("AndroidKeyStore", diagnostic)
        self.assertIn("MAX_STORED_CHARACTERS = 16_000", diagnostic)
        self.assertIn('startsWith("app.captchamesh.")', diagnostic)
        self.assertIn("throwable.getClass().getName()", diagnostic)
        self.assertNotIn("throwable.getMessage()", diagnostic)
        self.assertIn("Thread.setDefaultUncaughtExceptionHandler", application)
        self.assertIn("previous.uncaughtException(thread, throwable)", application)
        self.assertIn('android:name=".CaptchaMeshApp"', self.manifest)
        self.assertIn('secondaryButton("复制诊断", 0)', self.activity)
        self.assertIn("new LinearLayout.LayoutParams(0, dp(48), 1)", self.activity)
        self.assertIn('Toast.makeText(this, "脱敏诊断已复制"', self.activity)
        self.assertIn("DiagnosticLog.error", self.watch_service)
        self.assertIn("DiagnosticLog.error", self.relay_service)

    def test_release_version_is_0198(self):
        self.assertIn("versionCode = 30", self.build)
        self.assertIn('versionName = "0.19.8"', self.build)

    def test_settings_has_an_in_app_qr_pairing_action(self):
        self.assertIn('secondaryButton(', self.activity)
        self.assertIn('"扫码配对"', self.activity)
        self.assertIn('"重新扫码配对"', self.activity)
        self.assertIn("new ScanContract()", self.activity)
        self.assertIn("ScanOptions.QR_CODE", self.activity)
        self.assertIn("pairingScanner.launch(options)", self.activity)
        self.assertIn("options.setCaptureActivity(PairingCaptureActivity.class)", self.activity)
        self.assertIn("options.setOrientationLocked(true)", self.activity)
        self.assertIn('"captchamesh".equals(uri.getScheme())', self.activity)
        self.assertIn('"pair".equals(uri.getHost())', self.activity)
        self.assertIn("claimRelayPairing(uri)", self.activity)
        self.assertIn("android.permission.CAMERA", self.manifest)
        self.assertIn('android:name=".PairingCaptureActivity"', self.manifest)
        self.assertIn('android:screenOrientation="portrait"', self.manifest)
        self.assertIn('com.journeyapps:zxing-android-embedded:4.3.0', self.build)
        self.assertNotIn("用系统相机扫描电脑二维码", self.activity)

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
        self.assertIn(".remove(PREF_PENDING)", relay_store)
        self.assertIn("PREF_PENDING_QUEUE", relay_store)
        self.assertIn("RelayCrypto.decrypt", self.activity)
        self.assertIn("个人 Agent 需要你验证", relay_service)
        self.assertNotIn("websiteUrl", relay_service)

    def test_official_broker_domain_migrates_debug_value_once(self):
        self.assertIn('DEFAULT_BROKER = "https://mesh.vimalinx.com"', self.activity)
        self.assertIn('BROKER_DOMAIN_MIGRATION', self.activity)
        self.assertIn('"http://127.0.0.1:8890".equals(savedBroker)', self.activity)
        self.assertIn('"https://mesh.vimalinx.com"', self.watch_service)

    def test_saved_connection_is_loaded_before_first_workflow_refresh(self):
        on_create = re.search(
            r"protected void onCreate\(Bundle state\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(on_create)
        body = on_create.group("body")
        self.assertLess(body.index("loadConnectionConfiguration()"), body.index("buildUi()"))
        self.assertLess(body.index("loadConnectionConfiguration()"), body.index("refreshRegistrations()"))

        loader = re.search(
            r"private void loadConnectionConfiguration\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(loader)
        self.assertIn("loadSavedApiKey()", loader.group("body"))
        self.assertIn("configuredAuthorization", loader.group("body"))

        refresh = re.search(
            r"private void refreshRegistrations\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(refresh)
        self.assertNotIn("saveBroker()", refresh.group("body"))

    def test_missing_api_key_is_a_neutral_local_state(self):
        method = re.search(
            r"private void refreshRegistrations\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group("body")
        guard = body.index("if (apiAuthorization().isEmpty())")
        request = body.index("executor.submit")
        self.assertLess(guard, request)
        self.assertIn('renderConnectionState("待配置"', body)
        self.assertIn('"尚未配置 API Key"', body)
        self.assertIn("apiKeyField.setError(null)", body)

    def test_connection_errors_target_the_relevant_field_without_navigation(self):
        method = re.search(
            r"private void renderConnectionError\(Exception exception\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group("body")
        self.assertIn('detail.contains("HTTP 401")', body)
        self.assertIn('detail.contains("HTTP 403")', body)
        self.assertIn("brokerField.setError(authenticationError ? null : hint)", body)
        self.assertIn("apiKeyField.setError(authenticationError ? hint : null)", body)
        self.assertNotIn("selectPage", body)

    def test_diagnostics_distinguish_missing_and_invalid_api_keys(self):
        method = re.search(
            r"private void runDiagnostics\(\) \{(?P<body>.*?)\n    \}",
            self.activity,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group("body")
        self.assertIn("if (checkAuthorization.isEmpty())", body)
        self.assertIn('publishDiagnostic(1, "未配置"', body)
        self.assertIn('Http.get(http, checkBaseUrl + "/v1/stats", checkAuthorization)', body)


if __name__ == "__main__":
    unittest.main()
