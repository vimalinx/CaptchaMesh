package app.captchamesh;

import android.Manifest;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.RippleDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.InputType;
import android.text.TextUtils;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.DrawableRes;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.core.widget.TextViewCompat;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.chip.Chip;
import com.google.android.material.chip.ChipGroup;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import com.google.android.material.textfield.TextInputEditText;
import com.google.android.material.textfield.TextInputLayout;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

import okhttp3.OkHttpClient;

public class MainActivity extends AppCompatActivity implements Solver.Ui {
    private static final String PREFERENCES = "cm";
    private static final String API_KEY_CIPHERTEXT = "api_key_ciphertext";
    private static final String API_KEY_IV = "api_key_iv";
    private static final String LOCAL_RECORDS_CIPHERTEXT = "local_records_ciphertext";
    private static final String LOCAL_RECORDS_IV = "local_records_iv";
    private static final String KEYSTORE_ALIAS = "captchamesh_api_key_v1";
    private static final String STATE_SELECTED_PAGE = "selected_page";
    private static final String DEFAULT_BROKER = "https://mesh.vimalinx.com";
    private static final String BROKER_DOMAIN_MIGRATION = "broker_domain_migration_vimalinx_v1";
    private static final int REQUEST_NOTIFICATIONS = 2001;
    private static final int GCM_TAG_LENGTH_BITS = 128;
    private static final String[] TYPES = {
            "turnstile", "hcaptcha", "recaptcha_v2", "recaptcha_v3", "webview",
            "image_text", "coordinates", "grid", "rotate", "funcaptcha",
            "geetest_v3", "geetest_v4", "datadome", "amazon_waf"
    };

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ExecutorService diagnosticExecutor = Executors.newSingleThreadExecutor();
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(35, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .build();
    private final List<MaterialButton> startButtons = new ArrayList<>();
    private final List<LinearLayout> navigationItems = new ArrayList<>();
    private final List<FrameLayout> navigationIndicators = new ArrayList<>();
    private final List<ImageView> navigationIcons = new ArrayList<>();
    private final List<TextView> navigationLabels = new ArrayList<>();

    private TextInputEditText brokerInput;
    private TextInputEditText apiKeyInput;
    private TextInputLayout brokerField;
    private TextInputLayout apiKeyField;
    private LinearLayout registrationList;
    private LinearLayout challengeCard;
    private TextView connectionState;
    private TextView brokerSummary;
    private TextView relayStatus;
    private TextView registrationSummary;
    private TextView runBadge;
    private TextView runState;
    private TextView logView;
    private TextView challengeTitle;
    private TextView challengeSubtitle;
    private FrameLayout challengeHost;
    private FrameLayout pageHost;
    private View registrationsPage;
    private View taskPage;
    private View logPage;
    private View settingsPage;
    private View diagnosticsPage;
    private ScrollView taskScroll;
    private LinearLayout bottomNavigation;
    private MaterialButton refreshButton;
    private MaterialButton diagnosticButton;
    private TextView diagnosticSummary;
    private final List<TextView> diagnosticBadges = new ArrayList<>();
    private final List<TextView> diagnosticDetails = new ArrayList<>();
    private final List<TextView> timelineBadges = new ArrayList<>();
    private final List<TextView> timelineDetails = new ArrayList<>();

    private Solver solver;
    private volatile boolean active;
    private volatile boolean destroyed;
    private volatile String activeRunId;
    private volatile String configuredBaseUrl = DEFAULT_BROKER;
    private volatile String configuredAuthorization = "";
    static volatile boolean foregroundVisible;
    private int selectedPageId = R.id.nav_registrations;
    private String pendingRegistrationId;
    private String pendingRegistrationName;
    private volatile int timelineStage;
    private volatile boolean relayProcessing;
    private boolean pendingRelayPermission;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            selectedPageId = state.getInt(STATE_SELECTED_PAGE, R.id.nav_registrations);
        }
        configureWindow();
        solver = new Solver(this, this);
        setContentView(buildUi());
        handleIntent(getIntent());
        refreshRegistrations();
        resumeStoredRun();
    }

    private void configureWindow() {
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Tints.BACKGROUND);
        getWindow().setNavigationBarDividerColor(Tints.BACKGROUND);
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(
                getWindow(), getWindow().getDecorView());
        controller.setAppearanceLightStatusBars(false);
        controller.setAppearanceLightNavigationBars(false);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!active) refreshRegistrations();
        processPendingRelay();
    }

    @Override
    protected void onStart() {
        super.onStart();
        foregroundVisible = true;
        resumeStoredRun();
        processPendingRelay();
        if (RelayStore.load(this) != null
                && (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED)) {
            RelayWatchService.start(this);
        }
    }

    @Override
    protected void onStop() {
        foregroundVisible = false;
        super.onStop();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
        resumeStoredRun();
    }

    private void handleIntent(Intent intent) {
        if (intent == null) return;
        android.net.Uri data = intent.getData();
        if (Intent.ACTION_VIEW.equals(intent.getAction()) && data != null
                && "captchamesh".equals(data.getScheme()) && "pair".equals(data.getHost())) {
            intent.setData(null);
            claimRelayPairing(data);
            return;
        }
        if (CaptchaWatchService.ACTION_OPEN_CHALLENGE.equals(intent.getAction())
                || RelayWatchService.ACTION_OPEN_RELAY.equals(intent.getAction())) {
            selectPage(R.id.nav_task);
            taskScroll.post(() -> taskScroll.smoothScrollTo(0, 0));
            if (RelayWatchService.ACTION_OPEN_RELAY.equals(intent.getAction())) {
                processPendingRelay();
            }
        }
    }

    private View buildUi() {
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setBackgroundColor(Tints.BACKGROUND);
        ViewCompat.setOnApplyWindowInsetsListener(screen, (view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(
                    WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return windowInsets;
        });
        screen.addView(buildAppBar(), row());

        pageHost = new FrameLayout(this);
        registrationsPage = buildRegistrationsPage();
        taskPage = buildTaskPage();
        diagnosticsPage = buildDiagnosticsPage();
        logPage = buildLogPage();
        settingsPage = buildSettingsPage();
        pageHost.addView(registrationsPage, pageMatch());
        pageHost.addView(taskPage, pageMatch());
        pageHost.addView(diagnosticsPage, pageMatch());
        pageHost.addView(logPage, pageMatch());
        pageHost.addView(settingsPage, pageMatch());
        screen.addView(pageHost, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        bottomNavigation = new LinearLayout(this);
        bottomNavigation.setOrientation(LinearLayout.HORIZONTAL);
        bottomNavigation.setGravity(Gravity.CENTER);
        bottomNavigation.setPadding(dp(8), dp(3), dp(8), dp(3));
        bottomNavigation.setBackground(
                Tints.rounded(Tints.SURFACE_RAISED, 0, Tints.BORDER, dp(1)));
        bottomNavigation.setElevation(dp(4));
        addNavigationItem(R.id.nav_registrations, R.drawable.ic_list, "注册机");
        addNavigationItem(R.id.nav_task, R.drawable.ic_shield, "任务");
        addNavigationItem(R.id.nav_diagnostics, R.drawable.ic_diagnostics, "自检");
        addNavigationItem(R.id.nav_log, R.drawable.ic_history, "记录");
        addNavigationItem(R.id.nav_settings, R.drawable.ic_settings, "设置");
        screen.addView(bottomNavigation, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, navigationHeight()));
        selectPage(selectedPageId);

        ViewCompat.requestApplyInsets(screen);
        return screen;
    }

    private LinearLayout buildAppBar() {
        LinearLayout appBar = new LinearLayout(this);
        appBar.setGravity(Gravity.CENTER_VERTICAL);
        appBar.setPadding(adaptiveGutter(), dp(10), adaptiveGutter(), dp(10));
        appBar.setBackground(Tints.rounded(Tints.BACKGROUND, 0, Tints.BORDER, dp(1)));

        FrameLayout logoSurface = new FrameLayout(this);
        logoSurface.setBackground(Tints.rounded(Tints.ACCENT_SOFT, dp(12), Tints.ACCENT, dp(1)));
        ImageView logo = new ImageView(this);
        logo.setImageResource(R.drawable.ic_mesh);
        logo.setImageTintList(Tints.iconTint(Tints.ACCENT));
        logo.setPadding(dp(9), dp(9), dp(9), dp(9));
        logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        logoSurface.addView(logo, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        appBar.addView(logoSurface, new LinearLayout.LayoutParams(dp(32), dp(32)));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        titles.addView(text("CaptchaMesh", 20, Tints.TEXT, true));
        titles.addView(text("手动验证控制台", 11, Tints.TEXT_MUTED, false), row());
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        titleParams.leftMargin = dp(10);
        titleParams.rightMargin = dp(8);
        appBar.addView(titles, titleParams);

        connectionState = statusPill("连接中", Tints.WARNING, Tints.WARNING_SOFT);
        appBar.addView(connectionState, wrapEnd());
        return appBar;
    }

    private View buildRegistrationsPage() {
        LinearLayout page = pageContent();
        LinearLayout heading = new LinearLayout(this);
        heading.setGravity(Gravity.CENTER_VERTICAL);

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(text("注册机", 22, Tints.TEXT, true));
        registrationSummary = text("正在同步电脑端白名单", 12, Tints.TEXT_MUTED, false);
        labels.addView(registrationSummary, row());
        heading.addView(labels, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        refreshButton = secondaryButton("刷新", R.drawable.ic_refresh);
        refreshButton.setOnClickListener(view -> refreshRegistrations());
        heading.addView(refreshButton, wrapEnd());
        page.addView(heading);

        registrationList = new LinearLayout(this);
        registrationList.setOrientation(LinearLayout.VERTICAL);
        registrationList.addView(emptyPanel("正在读取注册机", "请稍候，正在连接电脑端 Broker。"));
        addTop(page, registrationList, 8);
        return scrollPage(page);
    }

    private View buildTaskPage() {
        LinearLayout page = pageContent();
        page.addView(pageHeading("任务", "当前运行状态与手动 CAPTCHA"));
        addTop(page, buildRunCard(), 12);
        challengeCard = buildChallengeCard();
        challengeCard.setVisibility(View.GONE);
        addTop(page, challengeCard, 12);
        addTop(page, buildCapabilityCard(), 12);
        taskScroll = scrollPage(page);
        return taskScroll;
    }

    private View buildDiagnosticsPage() {
        LinearLayout page = pageContent();
        page.addView(pageHeading("自检", "逐项确认手机、Hub、节点与后台通知"));

        LinearLayout card = card();
        LinearLayout header = sectionHeader(
                R.drawable.ic_diagnostics,
                text("连接诊断", 16, Tints.TEXT, true),
                text("不读取或显示 API Key、Token、Cookie", 12, Tints.TEXT_MUTED, false));
        diagnosticSummary = statusPill("未检查", Tints.TEXT_SECONDARY, Tints.SURFACE_MUTED);
        header.addView(diagnosticSummary, wrapEnd());
        card.addView(header);

        String[] labels = {
                "Broker 健康",
                "API 鉴权",
                "注册节点",
                "通知与后台"
        };
        String[] details = {
                "检查 /healthz 与协议版本",
                "验证当前加密保存的 API Key",
                "确认至少一台白名单节点在线",
                "检查通知权限与电池优化状态"
        };
        for (int index = 0; index < labels.length; index++) {
            if (index == 0) {
                addTop(card, diagnosticRow(index + 1, labels[index], details[index]), 12);
            } else {
                card.addView(sectionDivider(dp(44)));
                card.addView(diagnosticRow(index + 1, labels[index], details[index]));
            }
        }

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        diagnosticButton = primaryButton("开始自检", R.drawable.ic_diagnostics);
        diagnosticButton.setContentDescription("开始连接自检");
        diagnosticButton.setOnClickListener(view -> runDiagnostics());
        actions.addView(diagnosticButton, new LinearLayout.LayoutParams(
                0, dp(48), 1));
        MaterialButton systemSettings = secondaryButton("系统设置", R.drawable.ic_settings);
        systemSettings.setContentDescription("打开 CaptchaMesh 系统设置");
        systemSettings.setOnClickListener(view -> {
            Intent intent = new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(android.net.Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        });
        LinearLayout.LayoutParams settingsParams = new LinearLayout.LayoutParams(
                0, dp(48), 1);
        settingsParams.leftMargin = dp(8);
        actions.addView(systemSettings, settingsParams);
        addTop(card, actions, 14);
        addTop(page, card, 12);

        TextView note = text(
                "自检只发起只读健康请求和一次受限 CONNECT 探测；不会启动注册机或领取 CAPTCHA。",
                11, Tints.TEXT_MUTED, false);
        note.setLineSpacing(dp(2), 1.08f);
        addTop(page, note, 14);
        return scrollPage(page);
    }

    private View buildLogPage() {
        LinearLayout page = pageContent();
        page.addView(pageHeading("记录", "仅保存在本机的运行记录；认证值不会写入"));
        addTop(page, buildLogCard(), 12);
        return scrollPage(page);
    }

    private View buildSettingsPage() {
        LinearLayout page = pageContent();
        page.addView(pageHeading("设置", "配置电脑 Broker 与当前会话鉴权"));
        addTop(page, buildConnectionCard(), 12);
        TextView privacy = text("API Key 与本机运行记录均加密保存在这台手机；Token 和 Cookie 不写入记录。",
                11, Tints.TEXT_MUTED, false);
        privacy.setGravity(Gravity.CENTER);
        privacy.setLineSpacing(dp(2), 1.08f);
        addTop(page, privacy, 16);
        return scrollPage(page);
    }

    private LinearLayout buildConnectionCard() {
        LinearLayout card = card();
        brokerSummary = text("mesh.vimalinx.com · 默认 Hub", 12, Tints.TEXT_MUTED, false);
        LinearLayout header = sectionHeader(
                R.drawable.ic_server,
                text("电脑连接", 16, Tints.TEXT, true),
                brokerSummary);
        card.addView(header);

        brokerField = inputField(
                "Broker 地址",
                "默认连接公共 Hub；本机调试可填写 ADB reverse 地址。",
                false);
        brokerInput = (TextInputEditText) brokerField.getEditText();
        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        String savedBroker = preferences.getString("broker", DEFAULT_BROKER);
        if (!preferences.getBoolean(BROKER_DOMAIN_MIGRATION, false)) {
            if ("http://127.0.0.1:8890".equals(savedBroker)) {
                savedBroker = DEFAULT_BROKER;
            }
            preferences.edit()
                    .putString("broker", savedBroker)
                    .putBoolean(BROKER_DOMAIN_MIGRATION, true)
                    .apply();
        }
        brokerInput.setText(savedBroker);
        configuredBaseUrl = savedBroker.replaceAll("/+$", "");
        addTop(card, brokerField, 16);

        apiKeyField = inputField(
                "API Key",
                "仅加密保存在此手机；清空后点保存可移除。",
                true);
        apiKeyInput = (TextInputEditText) apiKeyField.getEditText();
        String savedApiKey = loadSavedApiKey();
        apiKeyInput.setText(savedApiKey);
        configuredAuthorization = savedApiKey.isEmpty() ? "" : "Bearer " + savedApiKey;
        addTop(card, apiKeyField, 12);

        RelayStore.Config relay = RelayStore.load(this);
        relayStatus = text(relay == null
                        ? "个人 Agent：未配对 · 用系统相机扫描电脑二维码"
                        : "个人 Agent：已端到端配对 · " + relay.nodeName,
                12, relay == null ? Tints.TEXT_MUTED : Tints.ACCENT, false);
        relayStatus.setContentDescription(relayStatus.getText());
        addTop(card, relayStatus, 14);

        MaterialButton apply = primaryButton("保存并刷新", R.drawable.ic_refresh);
        apply.setOnClickListener(view -> {
            saveBroker();
            selectPage(R.id.nav_registrations);
            refreshRegistrations();
        });
        addTop(card, apply, 12);
        return card;
    }

    private LinearLayout buildRunCard() {
        LinearLayout card = card();
        TextView subtitle = text("电脑进程与手机验证任务", 12, Tints.TEXT_MUTED, false);
        LinearLayout header = sectionHeader(
                R.drawable.ic_activity,
                text("当前运行", 16, Tints.TEXT, true),
                subtitle);
        runBadge = statusPill("待命", Tints.TEXT_SECONDARY, Tints.SURFACE_MUTED);
        header.addView(runBadge, wrapEnd());
        card.addView(header);

        runState = text("选择下方一个注册机开始", 15, Tints.TEXT_SECONDARY, false);
        runState.setLineSpacing(dp(2), 1.08f);
        addTop(card, runState, 14);

        addTop(card, sectionDivider(0), 16);
        addTop(card, sectionHeader(
                R.drawable.ic_history,
                text("任务时间线", 16, Tints.TEXT, true),
                text("从启动到交付，只保留一条清晰进度线", 12, Tints.TEXT_MUTED, false)), 16);
        String[] labels = {
                "任务已启动",
                "电脑准备环境",
                "等待 CAPTCHA",
                "人工验证回传",
                "注册完成"
        };
        String[] details = {
                "等待你从注册机列表启动",
                "浏览器和受控注册流程尚未开始",
                "挑战尚未到达手机",
                "等待你手动完成并回传",
                "等待电脑保存最终交付"
        };
        for (int index = 0; index < labels.length; index++) {
            if (index == 0) {
                addTop(card, timelineRow(index + 1, labels[index], details[index]), 12);
            } else {
                card.addView(sectionDivider(dp(44)));
                card.addView(timelineRow(index + 1, labels[index], details[index]));
            }
        }
        return card;
    }

    private LinearLayout timelineRow(int number, String label, String detail) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(11), 0, dp(11));

        TextView numberView = statusPill(String.valueOf(number), Tints.TEXT_MUTED, Tints.SURFACE_MUTED);
        numberView.setMinWidth(dp(34));
        numberView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(numberView, new LinearLayout.LayoutParams(dp(34), dp(34)));

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(text(label, 13, Tints.TEXT, true));
        TextView detailView = text(detail, 11, Tints.TEXT_MUTED, false);
        detailView.setLineSpacing(dp(1), 1.05f);
        labels.addView(detailView, row());
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        labelParams.leftMargin = dp(10);
        labelParams.rightMargin = dp(8);
        row.addView(labels, labelParams);

        TextView badge = statusPill("等待", Tints.TEXT_MUTED, Tints.SURFACE_MUTED);
        row.addView(badge, wrapEnd());
        timelineBadges.add(badge);
        timelineDetails.add(detailView);
        return row;
    }

    private LinearLayout diagnosticRow(int number, String label, String detail) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(11), 0, dp(11));

        TextView numberView = statusPill(String.valueOf(number), Tints.INFO, Tints.INFO_SOFT);
        numberView.setMinWidth(dp(34));
        numberView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(numberView, new LinearLayout.LayoutParams(dp(34), dp(34)));

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(text(label, 13, Tints.TEXT, true));
        TextView detailView = text(detail, 11, Tints.TEXT_MUTED, false);
        detailView.setLineSpacing(dp(1), 1.05f);
        labels.addView(detailView, row());
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        labelParams.leftMargin = dp(10);
        labelParams.rightMargin = dp(8);
        row.addView(labels, labelParams);

        TextView badge = statusPill("未检查", Tints.TEXT_MUTED, Tints.SURFACE_MUTED);
        row.addView(badge, wrapEnd());
        diagnosticBadges.add(badge);
        diagnosticDetails.add(detailView);
        return row;
    }

    private LinearLayout buildChallengeCard() {
        LinearLayout card = card();
        challengeTitle = text("等待 CAPTCHA", 16, Tints.TEXT, true);
        challengeSubtitle = text("等待挑战内容", 12, Tints.TEXT_MUTED, false);
        card.addView(sectionHeader(R.drawable.ic_shield, challengeTitle, challengeSubtitle));

        challengeHost = new FrameLayout(this);
        challengeHost.setBackground(Tints.rounded(Color.WHITE, dp(14), Tints.BORDER_STRONG, dp(1)));
        challengeHost.setClipToOutline(true);
        LinearLayout.LayoutParams hostParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, challengeHeight());
        hostParams.topMargin = dp(16);
        card.addView(challengeHost, hostParams);
        return card;
    }

    private LinearLayout buildLogCard() {
        LinearLayout card = card();
        TextView logSubtitle = text("仅显示本机脱敏事件", 12, Tints.TEXT_MUTED, false);
        LinearLayout header = sectionHeader(
                R.drawable.ic_history,
                text("最近活动", 16, Tints.TEXT, true),
                logSubtitle);
        MaterialButton clear = secondaryButton("清空", 0);
        clear.setContentDescription("清空本机运行记录");
        clear.setOnClickListener(view -> confirmClearLog());
        header.addView(clear, new LinearLayout.LayoutParams(dp(72), dp(48)));
        card.addView(header);

        String savedRecords = loadEncryptedPreference(
                LOCAL_RECORDS_CIPHERTEXT, LOCAL_RECORDS_IV);
        logView = text(savedRecords.isEmpty() ? "等待选择注册机" : savedRecords,
                12, Tints.ACCENT, false);
        logView.setTypeface(Typeface.MONOSPACE);
        logView.setLineSpacing(dp(3), 1.05f);
        logView.setTextIsSelectable(true);
        logView.setPadding(dp(14), dp(13), dp(14), dp(13));

        ScrollView logScroll = new ScrollView(this);
        logScroll.setFillViewport(true);
        logScroll.setBackground(Tints.rounded(Tints.SURFACE, dp(12), Tints.BORDER, dp(1)));
        logScroll.addView(logView);
        LinearLayout.LayoutParams logParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(164));
        logParams.topMargin = dp(16);
        card.addView(logScroll, logParams);
        return card;
    }

    private LinearLayout buildCapabilityCard() {
        LinearLayout card = card();
        card.addView(sectionHeader(
                R.drawable.ic_shield,
                text("接题能力", 16, Tints.TEXT, true),
                text("14 类人工挑战 · 按任务自动选择界面", 12, Tints.TEXT_MUTED, false)));
        addTop(card, capabilityGroup(
                "原生操作",
                new String[]{"图片文字", "坐标点击", "图片网格", "旋转校正"},
                Tints.ACCENT, Tints.ACCENT_SOFT), 14);
        addTop(card, capabilityGroup(
                "安全网页",
                new String[]{"Turnstile", "hCaptcha", "reCAPTCHA v2/v3", "网页验证", "FunCaptcha",
                        "GeeTest v3/v4", "DataDome", "Amazon WAF"},
                Tints.INFO, Tints.INFO_SOFT), 12);
        TextView note = text(
                "仅接收你主动启动的任务；图片临时加载，Token 与 Cookie 不写入本机记录。",
                11, Tints.TEXT_MUTED, false);
        note.setLineSpacing(dp(2), 1.08f);
        addTop(card, note, 12);
        return card;
    }

    private LinearLayout capabilityGroup(
            String label, String[] values, int foreground, int background) {
        LinearLayout group = new LinearLayout(this);
        group.setOrientation(LinearLayout.VERTICAL);
        group.addView(text(label, 10, Tints.TEXT_SECONDARY, true));
        ChipGroup chips = new ChipGroup(this);
        chips.setChipSpacingHorizontal(dp(6));
        chips.setChipSpacingVertical(dp(6));
        chips.setSingleLine(false);
        for (String value : values) chips.addView(chip(value, foreground, background));
        addTop(group, chips, 6);
        return group;
    }

    private void runDiagnostics() {
        if (diagnosticButton == null || !diagnosticButton.isEnabled()) return;
        saveBroker();
        diagnosticButton.setEnabled(false);
        diagnosticButton.setText("检查中");
        setPill(diagnosticSummary, "检查中", Tints.WARNING, Tints.WARNING_SOFT);
        for (int index = 0; index < diagnosticBadges.size(); index++) {
            setDiagnosticResult(index, "排队", "等待前一项检查完成", 1);
        }
        String checkBaseUrl = baseUrl();
        String checkAuthorization = apiAuthorization();
        diagnosticExecutor.submit(() -> {
            int passed = 0;
            int warnings = 0;
            JSONObject stats = null;

            try {
                JSONObject health = new JSONObject(
                        Http.get(http, checkBaseUrl + "/healthz", "").body);
                if (!health.optBoolean("ok") || !"3".equals(health.optString("protocolVersion"))) {
                    throw new IllegalStateException("protocol mismatch");
                }
                String host = android.net.Uri.parse(checkBaseUrl).getHost();
                boolean loopback = "127.0.0.1".equals(host) || "localhost".equals(host);
                publishDiagnostic(0, "正常",
                        loopback ? "Broker 协议 v3；ADB reverse 通道可达" : "Hub 协议 v3；HTTPS 通道可达",
                        2);
                passed++;
            } catch (Exception exception) {
                publishDiagnostic(0, "失败", connectionErrorHint(exception), 0);
            }

            try {
                stats = new JSONObject(
                        Http.get(http, checkBaseUrl + "/v1/stats", checkAuthorization).body);
                if (!"3".equals(stats.optString("protocolVersion"))) {
                    throw new IllegalStateException("protocol mismatch");
                }
                publishDiagnostic(1, "正常", "API Key 有效；鉴权接口返回协议 v3", 2);
                passed++;
            } catch (Exception exception) {
                publishDiagnostic(1, "失败", connectionErrorHint(exception), 0);
            }

            if (stats == null) {
                publishDiagnostic(2, "跳过", "API 鉴权失败，无法读取节点状态", 0);
            } else {
                JSONArray nodes = stats.optJSONArray("nodes");
                int total = nodes == null ? 0 : nodes.length();
                int online = 0;
                for (int index = 0; nodes != null && index < nodes.length(); index++) {
                    JSONObject node = nodes.optJSONObject(index);
                    if (node != null && node.optBoolean("online")) online++;
                }
                if (online > 0) {
                    publishDiagnostic(2, "正常", online + "/" + total + " 台节点在线，可启动白名单流程", 2);
                    passed++;
                } else {
                    publishDiagnostic(2, "失败", "没有在线注册节点；检查节点服务与 Key 重载", 0);
                }
            }

            boolean notifications = NotificationManagerCompat.from(this).areNotificationsEnabled();
            PowerManager power = getSystemService(PowerManager.class);
            boolean unrestricted = power != null
                    && power.isIgnoringBatteryOptimizations(getPackageName());
            if (!notifications) {
                publishDiagnostic(3, "失败", "通知权限未开启，后台到题时无法提醒", 0);
            } else if (!unrestricted) {
                publishDiagnostic(3, "需关注", "通知已开启；系统电池优化可能延迟后台提醒", 1);
                warnings++;
            } else {
                publishDiagnostic(3, "正常", "通知已开启，且未受系统电池优化限制", 2);
                passed++;
            }

            int finalPassed = passed;
            int finalWarnings = warnings;
            runOnUiThread(() -> {
                String summary = finalPassed + "/4 正常";
                int foreground = finalPassed == 4
                        ? Tints.ACCENT : (finalPassed + finalWarnings == 4 ? Tints.WARNING : Tints.DANGER);
                int background = finalPassed == 4
                        ? Tints.ACCENT_SOFT
                        : (finalPassed + finalWarnings == 4 ? Tints.WARNING_SOFT : Tints.DANGER_SOFT);
                setPill(diagnosticSummary, summary, foreground, background);
                diagnosticButton.setText("重新检查");
                diagnosticButton.setEnabled(true);
                diagnosticSummary.announceForAccessibility("连接自检完成，" + summary);
            });
        });
    }

    private void publishDiagnostic(int index, String badge, String detail, int tone) {
        runOnUiThread(() -> setDiagnosticResult(index, badge, detail, tone));
    }

    private void setDiagnosticResult(int index, String badge, String detail, int tone) {
        if (index < 0 || index >= diagnosticBadges.size()) return;
        int foreground = tone == 2 ? Tints.ACCENT : (tone == 1 ? Tints.WARNING : Tints.DANGER);
        int background = tone == 2
                ? Tints.ACCENT_SOFT : (tone == 1 ? Tints.WARNING_SOFT : Tints.DANGER_SOFT);
        setPill(diagnosticBadges.get(index), badge, foreground, background);
        diagnosticDetails.get(index).setText(detail);
    }

    private void setPill(TextView pill, String value, int foreground, int background) {
        pill.setText(value);
        pill.setTextColor(foreground);
        pill.setBackground(Tints.rounded(background, dp(99), foreground, dp(1)));
    }

    private void refreshRegistrations() {
        if (destroyed || active || brokerInput == null) return;
        saveBroker();
        renderConnectionState("连接中", Tints.WARNING, Tints.WARNING_SOFT);
        brokerField.setError(null);
        refreshButton.setEnabled(false);
        refreshButton.setText("读取中");
        registrationSummary.setText("正在同步电脑端白名单");
        executor.submit(() -> {
            try {
                JSONObject response = new JSONObject(
                        Http.get(http, baseUrl() + "/v1/registrations", apiAuthorization()).body);
                JSONArray registrations = response.getJSONArray("registrations");
                runOnUiThread(() -> renderRegistrations(registrations));
            } catch (Exception exception) {
                runOnUiThread(() -> renderConnectionError(exception));
            } finally {
                runOnUiThread(() -> {
                    refreshButton.setText("刷新");
                    refreshButton.setEnabled(!active);
                });
            }
        });
    }

    private void renderConnectionError(Exception exception) {
        renderConnectionState("连接失败", Tints.DANGER, Tints.DANGER_SOFT);
        brokerField.setError(connectionErrorHint(exception));
        selectPage(R.id.nav_settings);
        registrationSummary.setText("暂时无法读取电脑端列表");
        registrationList.removeAllViews();
        registrationList.addView(emptyPanel(
                "没有连接到电脑",
                connectionErrorHint(exception) + "\n" + concise(exception)));
    }

    private void renderRegistrations(JSONArray registrations) {
        renderConnectionState("已连接", Tints.ACCENT, Tints.ACCENT_SOFT);
        brokerField.setError(null);
        registrationList.removeAllViews();
        startButtons.clear();
        int enabledCount = 0;

        for (int index = 0; index < registrations.length(); index++) {
            JSONObject registration = registrations.optJSONObject(index);
            if (registration == null) continue;
            if (registration.optBoolean("enabled", false)) enabledCount++;
            registrationList.addView(
                    registrationItem(registration),
                    spacedRow(index == 0 ? 8 : 6));
        }

        registrationSummary.setText(getString(
                R.string.registration_count, registrations.length(), enabledCount));
        if (registrations.length() == 0) {
            registrationList.addView(emptyPanel(
                    "电脑端还没有注册机",
                    "先在 registrations.json 中登记允许启动的脚本。"));
        }
    }

    private LinearLayout registrationItem(JSONObject registration) {
        String id = registration.optString("id");
        String name = registration.optString("name", id);
        String summary = registration.optString(
                "summary", registration.optString("description"));
        boolean enabled = registration.optBoolean("enabled", false);

        LinearLayout item = new LinearLayout(this);
        item.setOrientation(LinearLayout.VERTICAL);
        item.setPadding(dp(12), dp(12), dp(12), dp(12));
        item.setBackground(Tints.rounded(Tints.SURFACE, dp(14), Tints.BORDER, dp(1)));

        LinearLayout identity = new LinearLayout(this);
        identity.setGravity(Gravity.CENTER_VERTICAL);
        FrameLayout iconSurface = new FrameLayout(this);
        iconSurface.setBackground(Tints.rounded(
                enabled ? Tints.ACCENT_SOFT : Tints.SURFACE_MUTED,
                dp(11),
                enabled ? Tints.ACCENT : Tints.BORDER_STRONG,
                dp(1)));
        ImageView icon = new ImageView(this);
        icon.setImageResource(R.drawable.ic_offer);
        icon.setImageTintList(Tints.iconTint(enabled ? Tints.ACCENT : Tints.TEXT_MUTED));
        icon.setPadding(dp(8), dp(8), dp(8), dp(8));
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        iconSurface.addView(icon, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        identity.addView(iconSurface, new LinearLayout.LayoutParams(dp(32), dp(32)));

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        TextView nameView = text(name, 15, Tints.TEXT, true);
        nameView.setSingleLine(true);
        nameView.setEllipsize(TextUtils.TruncateAt.END);
        TextViewCompat.setAutoSizeTextTypeUniformWithConfiguration(
                nameView, 11, 15, 1, android.util.TypedValue.COMPLEX_UNIT_SP);
        labels.addView(nameView);
        labels.addView(text(enabled ? "可启动" : "当前不可用", 10,
                enabled ? Tints.ACCENT : Tints.TEXT_MUTED, false), row());
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        labelParams.leftMargin = dp(10);
        labelParams.rightMargin = dp(8);
        identity.addView(labels, labelParams);

        MaterialButton start = primaryButton(
                enabled ? "启动" : "不可用",
                enabled ? R.drawable.ic_play : 0);
        start.setTag(enabled);
        start.setEnabled(enabled && !active);
        start.setContentDescription(enabled ? "启动 " + name : name + " 暂不可用");
        start.setOnClickListener(view -> startRegistration(id, name));
        startButtons.add(start);
        LinearLayout.LayoutParams startParams = new LinearLayout.LayoutParams(
                dp(enabled ? 96 : 92), dp(48));
        startParams.gravity = Gravity.CENTER_VERTICAL;
        identity.addView(start, startParams);
        item.addView(identity);

        if (!summary.isEmpty()) {
            TextView summaryView = text(summary, 12, Tints.TEXT_SECONDARY, false);
            summaryView.setLineSpacing(dp(1), 1.05f);
            summaryView.setSingleLine(true);
            summaryView.setEllipsize(TextUtils.TruncateAt.END);
            summaryView.setContentDescription(summary);
            addTop(item, summaryView, 7);
        }

        JSONArray provides = registration.optJSONArray("provides");
        JSONArray details = registration.optJSONArray("details");
        LinearLayout detailPanel = offerDetailPanel(registration, provides, details, enabled);
        detailPanel.setVisibility(View.GONE);

        JSONObject latest = registration.optJSONObject("latestRun");
        String disclosureLabel = (provides == null ? 0 : provides.length()) + " 项交付";
        if (latest != null) {
            String status = latest.optString("status");
            disclosureLabel += " · 上次" + statusText(status);
        }
        final String disclosureBaseLabel = disclosureLabel;
        LinearLayout disclosure = new LinearLayout(this);
        disclosure.setGravity(Gravity.CENTER_VERTICAL);
        disclosure.setMinimumHeight(dp(48));
        disclosure.setPadding(dp(12), dp(8), dp(10), dp(8));
        disclosure.setClickable(true);
        disclosure.setFocusable(true);
        disclosure.setBackground(Tints.rounded(Tints.SURFACE_MUTED, dp(12)));
        disclosure.setForeground(new RippleDrawable(
                Tints.ripple(), null, Tints.rounded(Color.WHITE, dp(12))));
        TextView disclosureText = text(
                getString(R.string.disclosure_collapsed, disclosureBaseLabel),
                11,
                Tints.TEXT_SECONDARY,
                true);
        disclosure.addView(disclosureText, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        ImageView disclosureIcon = new ImageView(this);
        disclosureIcon.setImageResource(R.drawable.ic_expand_more);
        disclosureIcon.setImageTintList(Tints.iconTint(Tints.TEXT_SECONDARY));
        disclosureIcon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        disclosure.addView(disclosureIcon, new LinearLayout.LayoutParams(dp(20), dp(20)));
        disclosure.setContentDescription("查看 " + name + " 的完整交付内容");
        disclosure.setOnClickListener(view -> {
            boolean expand = detailPanel.getVisibility() != View.VISIBLE;
            detailPanel.setVisibility(expand ? View.VISIBLE : View.GONE);
            disclosureIcon.setImageResource(
                    expand ? R.drawable.ic_expand_less : R.drawable.ic_expand_more);
            disclosureText.setText(getString(
                    expand ? R.string.disclosure_expanded : R.string.disclosure_collapsed,
                    disclosureBaseLabel));
            disclosure.setContentDescription(
                    (expand ? "收起 " : "查看 ") + name + " 的完整交付内容");
        });
        addTop(item, disclosure, 7);
        addTop(item, detailPanel, 7);
        return item;
    }

    private void startRegistration(String registrationId, String name) {
        if (active) {
            Toast.makeText(this, "当前已有注册机在运行", Toast.LENGTH_SHORT).show();
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            pendingRegistrationId = registrationId;
            pendingRegistrationName = name;
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    REQUEST_NOTIFICATIONS);
            Toast.makeText(this, "允许通知后，切到后台也能收到 CAPTCHA 提醒", Toast.LENGTH_LONG).show();
            return;
        }
        startRegistrationWithNotifications(registrationId, name);
    }

    private void startRegistrationWithNotifications(String registrationId, String name) {
        saveBroker();
        active = true;
        setTimelineStage(1, name + " 已由你手动启动");
        setInputsEnabled(false);
        setRunState("启动中", name + " · 正在启动电脑流程", Tints.WARNING, Tints.WARNING_SOFT);
        appendLog("启动 " + name);
        selectPage(R.id.nav_task);
        taskScroll.post(() -> taskScroll.smoothScrollTo(0, 0));
        executor.submit(() -> runRegistration(registrationId, name));
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_NOTIFICATIONS) return;
        if (pendingRelayPermission) {
            pendingRelayPermission = false;
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                RelayWatchService.start(this);
            } else {
                Toast.makeText(this, "未开启通知；打开 App 时仍可处理加密任务", Toast.LENGTH_LONG).show();
            }
            return;
        }
        String registrationId = pendingRegistrationId;
        String name = pendingRegistrationName;
        pendingRegistrationId = null;
        pendingRegistrationName = null;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED
                && registrationId != null && name != null) {
            startRegistrationWithNotifications(registrationId, name);
        } else {
            Toast.makeText(
                    this,
                    "未开启通知：暂不启动，以免后台错过 CAPTCHA",
                    Toast.LENGTH_LONG).show();
        }
    }

    private void runRegistration(String registrationId, String name) {
        boolean terminal = false;
        try {
            JSONObject started = new JSONObject(Http.post(
                    http,
                    baseUrl() + "/v1/registrations/" + android.net.Uri.encode(registrationId) + "/start",
                    "{}",
                    apiAuthorization()).body);
            activeRunId = started.getJSONObject("run").getString("runId");
            rememberActiveRun(activeRunId, name);
            CaptchaWatchService.start(this, activeRunId, name);
            uiTimelineStage(2, "电脑正在准备浏览器与注册环境");
            uiRunState("运行中", name + " · 电脑正在注册，等待 CAPTCHA",
                    Tints.INFO, Tints.INFO_SOFT);
            terminal = monitorRegistration(name);
        } catch (Exception exception) {
            if (activeRunId == null) {
                uiTimelineTerminal("failed", "启动电脑流程失败：" + concise(exception));
                uiRunState("失败", name + " · " + concise(exception), Tints.DANGER, Tints.DANGER_SOFT);
                appendLogOnUi("启动失败：" + concise(exception));
            } else if (!destroyed) {
                uiRunState("后台等待", name + " · 手机连接暂时中断，通知服务仍在重试",
                        Tints.WARNING, Tints.WARNING_SOFT);
                appendLogOnUi("前台监听中断，后台通知仍在运行");
            }
        } finally {
            finishRegistrationMonitor(terminal);
        }
    }

    private boolean monitorRegistration(String name) {
        String workerToken = "";
        int failures = 0;
        while (active && !destroyed) {
            try {
                JSONObject runResponse = new JSONObject(
                        Http.get(http, baseUrl() + "/v1/runs/" + activeRunId, apiAuthorization()).body);
                String status = runResponse.getJSONObject("run").getString("status");
                if (isTerminal(status)) {
                    uiTimelineTerminal(status, name + " · " + statusText(status));
                    int[] tone = statusTone(status);
                    uiRunState(statusText(status), name + " · " + statusText(status), tone[0], tone[1]);
                    appendLogOnUi(name + " 运行结束：" + statusText(status));
                    return true;
                }

                JSONObject taskCounts = runResponse.optJSONObject("tasks");
                int pending = taskCounts == null ? 0 : taskCounts.optInt("pending", 0);
                int leased = taskCounts == null ? 0 : taskCounts.optInt("leased", 0);
                int solved = taskCounts == null ? 0 : taskCounts.optInt("solved", 0);
                if (solved > 0) {
                    uiTimelineStage(4, "CAPTCHA 已回传，电脑正在保存最终交付");
                } else if (pending + leased > 0 || "captcha".equals(status)) {
                    uiTimelineStage(3, "CAPTCHA 已到达，等待你在手机手动完成");
                } else if ("running".equals(status) || "starting".equals(status)) {
                    uiTimelineStage(2, "电脑流程运行中，尚未出现 CAPTCHA");
                }

                if (!foregroundVisible) {
                    sleepQuietly(1000);
                    continue;
                }
                if (workerToken.isEmpty()) workerToken = joinWorker();
                JSONObject pollBody = new JSONObject()
                        .put("runId", activeRunId)
                        .put("waitSeconds", 4);
                Http.Result polled = Http.post(
                        http, baseUrl() + "/v1/workers/poll", pollBody.toString(),
                        "Worker " + workerToken);
                if (polled.code == 204 || polled.body.isEmpty()) continue;
                CaptchaTask task = new CaptchaTask(new JSONObject(polled.body));
                if (!foregroundVisible || destroyed) {
                    returnTaskForForeground(task, workerToken);
                    continue;
                }
                uiRunState("待验证", name + " · 请在手机完成 " + friendlyCaptcha(task.type),
                        Tints.WARNING, Tints.WARNING_SOFT);
                uiTimelineStage(3, friendlyCaptcha(task.type) + " 已打开，等待人工提交");
                appendLogOnUi("收到 " + task.type + " · " + task.host());
                submitSolution(task, workerToken);
                uiTimelineStage(4, "人工验证结果已回传，电脑继续运行");
                uiRunState("已回传", name + " · CAPTCHA 已完成，电脑继续运行",
                        Tints.ACCENT, Tints.ACCENT_SOFT);
                failures = 0;
            } catch (Exception exception) {
                if (destroyed || !active) break;
                failures++;
                workerToken = "";
                uiRunState("后台等待", name + " · 连接中断，正在重试",
                        Tints.WARNING, Tints.WARNING_SOFT);
                if (failures == 1 || failures % 5 == 0) {
                    appendLogOnUi("监听重试：" + concise(exception));
                }
                sleepQuietly(Math.min(10_000, 1500L * failures));
            }
        }
        return false;
    }

    private void finishRegistrationMonitor(boolean terminal) {
        if (terminal || activeRunId == null) {
            active = false;
            activeRunId = null;
            clearStoredRun();
            CaptchaWatchService.stop(this);
            if (!destroyed) runOnUiThread(() -> {
                setInputsEnabled(true);
                challengeCard.setVisibility(View.GONE);
                refreshRegistrations();
            });
        } else if (destroyed) {
            active = false;
        }
    }

    private void rememberActiveRun(String runId, String name) {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                .putString(CaptchaWatchService.PREF_ACTIVE_RUN_ID, runId)
                .putString(CaptchaWatchService.PREF_ACTIVE_RUN_NAME, name)
                .apply();
    }

    private void clearStoredRun() {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                .remove(CaptchaWatchService.PREF_ACTIVE_RUN_ID)
                .remove(CaptchaWatchService.PREF_ACTIVE_RUN_NAME)
                .apply();
    }

    private void resumeStoredRun() {
        if (active || destroyed || pageHost == null) return;
        String runId = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(CaptchaWatchService.PREF_ACTIVE_RUN_ID, "");
        String name = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(CaptchaWatchService.PREF_ACTIVE_RUN_NAME, "注册任务");
        if (runId.isEmpty()) return;
        active = true;
        activeRunId = runId;
        setInputsEnabled(false);
        setRunState("后台等待", name + " · 正在恢复本次任务监听", Tints.INFO, Tints.INFO_SOFT);
        setTimelineStage(2, "已恢复用户启动的 run，正在读取当前阶段");
        CaptchaWatchService.start(this, runId, name);
        executor.submit(() -> finishRegistrationMonitor(monitorRegistration(name)));
    }

    private void returnTaskForForeground(CaptchaTask task, String workerToken) throws Exception {
        JSONObject body = new JSONObject()
                .put("taskId", task.id)
                .put("status", "failed")
                .put("errorCode", "ERROR_WORKER_BACKGROUND")
                .put("errorDescription", "phone moved to background before challenge display")
                .put("retryable", true);
        Http.post(http, baseUrl() + "/v1/workers/submit", body.toString(),
                "Worker " + workerToken);
    }

    private void sleepQuietly(long milliseconds) {
        try {
            Thread.sleep(milliseconds);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    private String joinWorker() throws Exception {
        JSONArray types = new JSONArray();
        for (String type : TYPES) types.put(type);
        JSONObject body = new JSONObject()
                .put("name", "manual-" + Build.MODEL.replaceAll("\\s+", "_"))
                .put("domains", new JSONArray())
                .put("types", types)
                .put("appVersion", getPackageManager().getPackageInfo(getPackageName(), 0).versionName)
                .put("device", Build.MANUFACTURER + " " + Build.MODEL);
        return new JSONObject(Http.post(
                http, baseUrl() + "/v1/workers/join", body.toString(), apiAuthorization()).body)
                .getString("workerToken");
    }

    private void submitSolution(CaptchaTask task, String workerToken) throws Exception {
        try {
            Map<String, Bitmap> assets = new HashMap<>();
            for (String name : task.assetNames()) {
                byte[] bytes = Http.getBytes(
                        http,
                        baseUrl() + "/v1/assets/" + android.net.Uri.encode(task.assetId(name)),
                        "Worker " + workerToken);
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                if (bitmap == null) throw new IllegalStateException("challenge_asset_invalid");
                assets.put(name, bitmap);
            }
            Solver.Solution solved = solver.solve(task, assets);
            JSONObject body = new JSONObject()
                    .put("taskId", task.id)
                    .put("status", "ready")
                    .put("solution", solved.value);
            Http.post(http, baseUrl() + "/v1/workers/submit", body.toString(),
                    "Worker " + workerToken);
            appendLogOnUi(task.type + " 已回传给电脑");
        } catch (Exception exception) {
            JSONObject body = new JSONObject()
                    .put("taskId", task.id)
                    .put("status", "failed")
                    .put("errorCode", "ERROR_MANUAL_CAPTCHA_FAILED")
                    .put("errorDescription", concise(exception))
                    .put("retryable", false);
            Http.post(http, baseUrl() + "/v1/workers/submit", body.toString(),
                    "Worker " + workerToken);
            throw exception;
        }
    }

    @Override
    public void showChallenge(View challenge, CaptchaTask task) {
        ViewParent parent = challenge.getParent();
        if (parent instanceof ViewGroup) ((ViewGroup) parent).removeView(challenge);
        challengeHost.removeAllViews();
        View displayed = challenge;
        if (task.structured()) {
            ScrollView scroll = new ScrollView(this);
            scroll.setFillViewport(true);
            scroll.setOverScrollMode(View.OVER_SCROLL_NEVER);
            scroll.addView(challenge, new ScrollView.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            displayed = scroll;
        }
        challengeHost.addView(displayed, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        challengeTitle.setText(getString(
                R.string.challenge_title, friendlyCaptcha(task.type), task.host()));
        challengeSubtitle.setText(task.structured()
                ? "仅显示题目与必要操作；可撤销后再提交"
                : (task.type.equals("webview")
                        ? "兼容模式：在受限网页区域手动完成"
                        : "仅加载挑战组件，不打开目标站完整页面"));
        challengeCard.setVisibility(View.VISIBLE);
        selectPage(R.id.nav_task);
        taskScroll.post(() -> taskScroll.smoothScrollTo(0, challengeCard.getTop()));
    }

    @Override
    public void clearChallenge(View challenge) {
        challengeHost.removeAllViews();
        challengeCard.setVisibility(View.GONE);
    }

    private void setInputsEnabled(boolean enabled) {
        brokerField.setEnabled(enabled);
        apiKeyField.setEnabled(enabled);
        refreshButton.setEnabled(enabled);
        for (MaterialButton button : startButtons) {
            button.setEnabled(enabled && Boolean.TRUE.equals(button.getTag()));
        }
    }

    private void saveBroker() {
        String broker = brokerInput == null ? "" : brokerInput.getText().toString().trim();
        if (!broker.isEmpty()) {
            configuredBaseUrl = broker.replaceAll("/+$", "");
            getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                    .putString("broker", configuredBaseUrl).apply();
            brokerSummary.setText(getString(
                    R.string.broker_summary,
                    configuredBaseUrl.replaceFirst("^https?://", ""),
                    configuredBaseUrl.contains("127.0.0.1")
                            ? "ADB 本机通道"
                            : (configuredBaseUrl.contains("mesh.vimalinx.com")
                                    ? "默认 Hub"
                                    : "网络连接")));
        }
        String key = apiKeyInput == null ? "" : apiKeyInput.getText().toString();
        configuredAuthorization = key.isEmpty() ? "" : "Bearer " + key;
        saveApiKey(key);
    }

    private String loadSavedApiKey() {
        return loadEncryptedPreference(API_KEY_CIPHERTEXT, API_KEY_IV);
    }

    private String loadEncryptedPreference(String ciphertextName, String ivName) {
        String ciphertext = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(ciphertextName, "");
        String iv = getSharedPreferences(PREFERENCES, MODE_PRIVATE).getString(ivName, "");
        if (ciphertext.isEmpty() || iv.isEmpty()) return "";
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, keystoreKey(), new GCMParameterSpec(
                    GCM_TAG_LENGTH_BITS, Base64.decode(iv, Base64.NO_WRAP)));
            return new String(cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)),
                    StandardCharsets.UTF_8);
        } catch (Exception exception) {
            getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                    .remove(ciphertextName).remove(ivName).apply();
            return "";
        }
    }

    private void saveApiKey(String value) {
        if (value.isEmpty()) {
            clearSavedApiKey();
            return;
        }
        saveEncryptedPreference(API_KEY_CIPHERTEXT, API_KEY_IV, value);
    }

    private void saveEncryptedPreference(String ciphertextName, String ivName, String value) {
        if (value.isEmpty()) {
            getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                    .remove(ciphertextName).remove(ivName).apply();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, keystoreKey());
            getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                    .putString(ciphertextName,
                            Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)),
                                    Base64.NO_WRAP))
                    .putString(ivName, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                    .apply();
        } catch (Exception exception) {
            Toast.makeText(this, "无法安全保存本机数据；本次仍可使用", Toast.LENGTH_LONG).show();
        }
    }

    private void clearSavedApiKey() {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                .remove(API_KEY_CIPHERTEXT)
                .remove(API_KEY_IV)
                .apply();
    }

    private SecretKey keystoreKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        SecretKey existing = (SecretKey) keyStore.getKey(KEYSTORE_ALIAS, null);
        if (existing != null) return existing;
        KeyGenerator generator = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEYSTORE_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }

    private String baseUrl() {
        return configuredBaseUrl;
    }

    private String apiAuthorization() {
        return configuredAuthorization;
    }

    private boolean isTerminal(String status) {
        return status.equals("succeeded") || status.equals("failed")
                || status.equals("cancelled") || status.equals("interrupted");
    }

    private String statusText(String status) {
        switch (status) {
            case "starting": return "启动中";
            case "running": return "运行中";
            case "captcha": return "等待手机验证";
            case "succeeded": return "已完成";
            case "failed": return "失败";
            case "cancelled": return "已停止";
            case "interrupted": return "已中断";
            default: return status;
        }
    }

    private String friendlyCaptcha(String type) {
        switch (type) {
            case "turnstile": return "Turnstile";
            case "hcaptcha": return "hCaptcha";
            case "recaptcha_v2": return "reCAPTCHA v2";
            case "recaptcha_v3": return "reCAPTCHA v3";
            case "image_text": return "图片文字";
            case "coordinates": return "坐标点击";
            case "grid": return "图片网格";
            case "rotate": return "旋转校正";
            case "funcaptcha": return "FunCaptcha";
            case "geetest_v3": return "GeeTest v3";
            case "geetest_v4": return "GeeTest v4";
            case "datadome": return "DataDome";
            case "amazon_waf": return "Amazon WAF";
            case "webview": return "网页验证";
            default: return type;
        }
    }

    private int[] statusTone(String status) {
        switch (status) {
            case "succeeded": return new int[]{Tints.ACCENT, Tints.ACCENT_SOFT};
            case "failed": return new int[]{Tints.DANGER, Tints.DANGER_SOFT};
            case "starting":
            case "captcha": return new int[]{Tints.WARNING, Tints.WARNING_SOFT};
            case "running": return new int[]{Tints.INFO, Tints.INFO_SOFT};
            default: return new int[]{Tints.TEXT_SECONDARY, Tints.SURFACE_MUTED};
        }
    }

    private void renderConnectionState(String value, int foreground, int background) {
        connectionState.setText(value);
        connectionState.setTextColor(foreground);
        connectionState.setBackground(Tints.rounded(background, dp(99), foreground, dp(1)));
    }

    private void setRunState(String badge, String value, int foreground, int background) {
        setPill(runBadge, badge, foreground, background);
        runState.setText(value);
        runState.setTextColor(Tints.TEXT_SECONDARY);
    }

    private void uiRunState(String badge, String value, int foreground, int background) {
        runOnUiThread(() -> setRunState(badge, value, foreground, background));
    }

    private void setTimelineStage(int stage, String currentDetail) {
        timelineStage = Math.max(0, Math.min(stage, timelineBadges.size()));
        for (int index = 0; index < timelineBadges.size(); index++) {
            int step = index + 1;
            TextView badge = timelineBadges.get(index);
            if (step < timelineStage) {
                setPill(badge, "完成", Tints.ACCENT, Tints.ACCENT_SOFT);
            } else if (step == timelineStage) {
                setPill(badge, "进行中", Tints.WARNING, Tints.WARNING_SOFT);
                timelineDetails.get(index).setText(currentDetail);
            } else {
                setPill(badge, "等待", Tints.TEXT_MUTED, Tints.SURFACE_MUTED);
            }
        }
    }

    private void uiTimelineStage(int stage, String detail) {
        runOnUiThread(() -> setTimelineStage(stage, detail));
    }

    private void setTimelineTerminal(String status, String detail) {
        boolean succeeded = "succeeded".equals(status);
        int terminalIndex = succeeded ? timelineBadges.size() : Math.max(1, timelineStage);
        for (int index = 0; index < timelineBadges.size(); index++) {
            int step = index + 1;
            TextView badge = timelineBadges.get(index);
            if (succeeded || step < terminalIndex) {
                setPill(badge, "完成", Tints.ACCENT, Tints.ACCENT_SOFT);
            } else if (step == terminalIndex) {
                String label = "cancelled".equals(status) ? "已停止" : "失败";
                int foreground = "cancelled".equals(status) ? Tints.WARNING : Tints.DANGER;
                int background = "cancelled".equals(status) ? Tints.WARNING_SOFT : Tints.DANGER_SOFT;
                setPill(badge, label, foreground, background);
                timelineDetails.get(index).setText(detail);
            } else {
                setPill(badge, "未执行", Tints.TEXT_MUTED, Tints.SURFACE_MUTED);
            }
        }
        if (succeeded && !timelineDetails.isEmpty()) {
            timelineDetails.get(timelineDetails.size() - 1).setText(detail);
        }
    }

    private void uiTimelineTerminal(String status, String detail) {
        runOnUiThread(() -> setTimelineTerminal(status, detail));
    }

    private void appendLogOnUi(String value) {
        runOnUiThread(() -> appendLog(value));
    }

    private void appendLog(String value) {
        String time = android.text.format.DateFormat.format(
                "HH:mm:ss", System.currentTimeMillis()).toString();
        String existing = logView.getText().toString();
        String updated = getString(
                R.string.log_entry,
                time,
                value,
                existing.substring(0, Math.min(existing.length(), 6000)));
        logView.setText(updated);
        saveEncryptedPreference(LOCAL_RECORDS_CIPHERTEXT, LOCAL_RECORDS_IV, updated);
    }

    private String concise(Exception exception) {
        String message = exception.getMessage();
        if (message == null || message.isEmpty()) return exception.getClass().getSimpleName();
        return message.length() > 120 ? message.substring(0, 120) : message;
    }

    private String connectionErrorHint(Exception exception) {
        String detail = concise(exception);
        if (detail.contains("HTTP 401")) return "API Key 无效，请重新粘贴后保存";
        if (detail.contains("HTTP 403")) return "当前 API Key 没有访问权限";
        if (detail.contains("CLEARTEXT")) return "公网 Broker 必须使用 HTTPS";
        return "无法连接 Broker，请检查地址与服务状态";
    }

    private void confirmClearLog() {
        new MaterialAlertDialogBuilder(this)
                .setTitle("清空本机记录？")
                .setMessage("只会移除这台手机上的脱敏运行记录，API Key 和电脑端数据不受影响。")
                .setNegativeButton("取消", null)
                .setPositiveButton("清空", (dialog, which) -> clearLocalLog())
                .show();
    }

    private void clearLocalLog() {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                .remove(LOCAL_RECORDS_CIPHERTEXT)
                .remove(LOCAL_RECORDS_IV)
                .apply();
        logView.setText("等待选择注册机");
        Toast.makeText(this, "本机记录已清空", Toast.LENGTH_SHORT).show();
    }

    private TextInputLayout inputField(String label, String helper, boolean password) {
        TextInputLayout field = new TextInputLayout(this);
        field.setHint(label);
        field.setHelperText(helper);
        field.setBoxBackgroundMode(TextInputLayout.BOX_BACKGROUND_OUTLINE);
        field.setBoxBackgroundColor(Tints.SURFACE);
        field.setBoxStrokeWidth(dp(1));
        field.setBoxStrokeWidthFocused(dp(2));
        field.setBoxStrokeColorStateList(new ColorStateList(
                new int[][]{
                        new int[]{android.R.attr.state_focused},
                        new int[]{-android.R.attr.state_enabled},
                        new int[]{}
                },
                new int[]{Tints.ACCENT, Tints.BORDER, Tints.BORDER_STRONG}));
        field.setDefaultHintTextColor(ColorStateList.valueOf(Tints.TEXT_MUTED));
        field.setHintTextColor(ColorStateList.valueOf(Tints.ACCENT));
        field.setHelperTextColor(ColorStateList.valueOf(Tints.TEXT_MUTED));
        field.setErrorTextColor(ColorStateList.valueOf(Tints.DANGER));
        field.setBoxCornerRadii(dp(12), dp(12), dp(12), dp(12));

        TextInputEditText input = new TextInputEditText(this);
        input.setSingleLine(true);
        input.setTextSize(15);
        input.setTextColor(Tints.TEXT);
        input.setHintTextColor(Tints.TEXT_MUTED);
        input.setMinimumHeight(dp(56));
        if (password) {
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            field.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
            input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
            input.setAutofillHints();
            field.setEndIconMode(TextInputLayout.END_ICON_PASSWORD_TOGGLE);
            field.setEndIconTintList(Tints.iconTint(Tints.TEXT_SECONDARY));
        } else {
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        }
        field.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return field;
    }

    private LinearLayout pageContent() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        int gutter = adaptiveGutter();
        page.setPadding(gutter, dp(16), gutter, dp(24));
        return page;
    }

    private LinearLayout pageHeading(String titleValue, String subtitleValue) {
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.VERTICAL);
        heading.addView(text(titleValue, 22, Tints.TEXT, true));
        heading.addView(text(subtitleValue, 12, Tints.TEXT_MUTED, false), row());
        return heading;
    }

    private ScrollView scrollPage(LinearLayout content) {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(true);
        scroll.setBackgroundColor(Tints.BACKGROUND);
        scroll.addView(content, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    private FrameLayout.LayoutParams pageMatch() {
        return new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }

    private void addNavigationItem(
            int itemId, @DrawableRes int iconResource, String labelValue) {
        boolean landscape = isLandscape();
        LinearLayout item = new LinearLayout(this);
        item.setId(itemId);
        item.setTag(itemId);
        item.setOrientation(landscape ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        item.setGravity(Gravity.CENTER);
        item.setPadding(dp(4), dp(2), dp(4), dp(2));
        item.setMinimumHeight(dp(48));
        item.setClickable(true);
        item.setFocusable(true);
        item.setForeground(new RippleDrawable(
                Tints.ripple(), null, Tints.rounded(Color.WHITE, dp(18))));

        FrameLayout indicator = new FrameLayout(this);
        indicator.setForeground(new RippleDrawable(
                Tints.ripple(), null, Tints.rounded(Color.WHITE, dp(99))));
        ImageView icon = new ImageView(this);
        icon.setImageResource(iconResource);
        icon.setImageTintList(Tints.iconTint(Tints.TEXT_MUTED));
        icon.setPadding(dp(5), dp(4), dp(5), dp(4));
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        indicator.addView(icon, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        item.addView(indicator, new LinearLayout.LayoutParams(dp(34), dp(26)));

        TextView label = text(labelValue, 10, Tints.TEXT_MUTED, false);
        label.setGravity(Gravity.CENTER);
        label.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        if (landscape) labelParams.leftMargin = dp(5);
        else labelParams.topMargin = dp(1);
        item.addView(label, labelParams);

        item.setContentDescription(labelValue);
        item.setOnClickListener(view -> selectPage(itemId));
        LinearLayout.LayoutParams itemParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
        itemParams.leftMargin = dp(2);
        itemParams.rightMargin = dp(2);
        bottomNavigation.addView(item, itemParams);
        navigationItems.add(item);
        navigationIndicators.add(indicator);
        navigationIcons.add(icon);
        navigationLabels.add(label);
    }

    private void selectPage(int itemId) {
        selectedPageId = itemId;
        showPage(itemId);
        for (int index = 0; index < navigationItems.size(); index++) {
            LinearLayout item = navigationItems.get(index);
            boolean selected = itemId == (Integer) item.getTag();
            FrameLayout indicator = navigationIndicators.get(index);
            ImageView icon = navigationIcons.get(index);
            TextView label = navigationLabels.get(index);
            item.setSelected(selected);
            item.setBackground(null);
            indicator.setBackground(selected
                    ? Tints.rounded(Tints.ACCENT_SOFT, dp(99), Tints.ACCENT, dp(1))
                    : null);
            icon.setImageTintList(Tints.iconTint(selected ? Tints.ACCENT : Tints.TEXT_MUTED));
            label.setTextColor(selected ? Tints.ACCENT : Tints.TEXT_MUTED);
            label.setTypeface(Typeface.create(
                    selected ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
            item.setContentDescription(
                    label.getText() + (selected ? "，当前页面" : ""));
        }
    }

    private void showPage(int itemId) {
        registrationsPage.setVisibility(
                itemId == R.id.nav_registrations ? View.VISIBLE : View.GONE);
        taskPage.setVisibility(itemId == R.id.nav_task ? View.VISIBLE : View.GONE);
        diagnosticsPage.setVisibility(itemId == R.id.nav_diagnostics ? View.VISIBLE : View.GONE);
        logPage.setVisibility(itemId == R.id.nav_log ? View.VISIBLE : View.GONE);
        settingsPage.setVisibility(itemId == R.id.nav_settings ? View.VISIBLE : View.GONE);
    }

    private LinearLayout offerDetailPanel(
            JSONObject registration, JSONArray provides, JSONArray details, boolean enabled) {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(12), dp(12), dp(12), dp(12));
        panel.setBackground(Tints.rounded(Tints.SURFACE_RAISED, dp(12), Tints.BORDER, dp(1)));
        panel.addView(text("完整交付内容", 12, Tints.TEXT, true));

        JSONArray captchaTypes = registration.optJSONArray("captchaTypes");
        if (captchaTypes != null && captchaTypes.length() > 0) {
            ChipGroup challenges = new ChipGroup(this);
            challenges.setChipSpacingHorizontal(dp(6));
            challenges.setChipSpacingVertical(dp(6));
            challenges.setSingleLine(false);
            for (int index = 0; index < captchaTypes.length(); index++) {
                challenges.addView(chip(
                        friendlyCaptcha(captchaTypes.optString(index)),
                        Tints.INFO,
                        Tints.INFO_SOFT));
            }
            addTop(panel, text("可能需要手动验证", 10, Tints.TEXT_SECONDARY, true), 10);
            addTop(panel, challenges, 6);
        }

        ChipGroup deliverables = new ChipGroup(this);
        deliverables.setChipSpacingHorizontal(dp(6));
        deliverables.setChipSpacingVertical(dp(6));
        deliverables.setSingleLine(false);
        if (provides != null) {
            for (int index = 0; index < provides.length(); index++) {
                deliverables.addView(chip(
                        provides.optString(index),
                        enabled ? Tints.ACCENT : Tints.TEXT_SECONDARY,
                        enabled ? Tints.ACCENT_SOFT : Tints.SURFACE_MUTED));
            }
        }
        if (deliverables.getChildCount() > 0) addTop(panel, deliverables, 8);

        String summary = registration.optString("summary");
        if (!summary.isEmpty()) addTop(panel, detailRow("概览", summary), 10);
        if (details != null) {
            for (int index = 0; index < details.length(); index++) {
                JSONObject detail = details.optJSONObject(index);
                if (detail == null) continue;
                String label = detail.optString("label");
                String value = detail.optString("value");
                if (!label.isEmpty() && !value.isEmpty()) {
                    addTop(panel, detailRow(label, value), 9);
                }
            }
        }
        String source = registration.optString("source");
        if (!source.isEmpty()) {
            String availability = registration.optBoolean("online", true) ? "在线" : "离线";
            addTop(panel, detailRow("登记节点", source + " · " + availability), 9);
        }
        return panel;
    }

    private LinearLayout detailRow(String labelValue, String value) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.addView(text(labelValue, 10, Tints.ACCENT, true));
        TextView content = text(value, 12, Tints.TEXT_SECONDARY, false);
        content.setLineSpacing(dp(2), 1.08f);
        addTop(row, content, 3);
        return row;
    }

    private LinearLayout sectionHeader(
            @DrawableRes int iconResource, TextView title, TextView subtitle) {
        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);

        FrameLayout iconSurface = new FrameLayout(this);
        iconSurface.setBackground(Tints.rounded(Tints.SURFACE_MUTED, dp(10)));
        ImageView icon = new ImageView(this);
        icon.setImageResource(iconResource);
        icon.setImageTintList(Tints.iconTint(Tints.TEXT_SECONDARY));
        icon.setPadding(dp(9), dp(9), dp(9), dp(9));
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        iconSurface.addView(icon, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        header.addView(iconSurface, new LinearLayout.LayoutParams(dp(34), dp(34)));

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(title);
        labels.addView(subtitle, row());
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        labelParams.leftMargin = dp(12);
        labelParams.rightMargin = dp(10);
        header.addView(labels, labelParams);
        return header;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(16), dp(16), dp(16));
        card.setBackground(Tints.rounded(Tints.SURFACE_RAISED, dp(16), Tints.BORDER, dp(1)));
        card.setElevation(dp(1));
        return card;
    }

    private View sectionDivider(int startDp) {
        View divider = new View(this);
        divider.setBackgroundColor(Tints.BORDER);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(1));
        params.leftMargin = startDp;
        divider.setLayoutParams(params);
        divider.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return divider;
    }

    private LinearLayout emptyPanel(String title, String detail) {
        LinearLayout empty = new LinearLayout(this);
        empty.setOrientation(LinearLayout.VERTICAL);
        empty.setGravity(Gravity.CENTER);
        empty.setPadding(dp(16), dp(20), dp(16), dp(20));
        empty.setBackground(Tints.rounded(Tints.SURFACE, dp(14), Tints.BORDER, dp(1)));
        TextView heading = text(title, 14, Tints.TEXT_SECONDARY, true);
        heading.setGravity(Gravity.CENTER);
        empty.addView(heading);
        TextView message = text(detail, 12, Tints.TEXT_MUTED, false);
        message.setGravity(Gravity.CENTER);
        message.setLineSpacing(dp(2), 1.08f);
        addTop(empty, message, 6);
        return empty;
    }

    private TextView statusPill(String value, int foreground, int background) {
        TextView pill = text(value, 11, foreground, true);
        pill.setGravity(Gravity.CENTER);
        pill.setMinHeight(dp(32));
        pill.setPadding(dp(12), dp(6), dp(12), dp(6));
        pill.setBackground(Tints.rounded(background, dp(99), foreground, dp(1)));
        return pill;
    }

    private Chip chip(String value, int foreground, int background) {
        Chip chip = new Chip(this);
        chip.setText(value);
        chip.setTextSize(10);
        chip.setTextColor(foreground);
        chip.setChipBackgroundColor(ColorStateList.valueOf(background));
        chip.setChipStrokeColor(ColorStateList.valueOf(foreground));
        chip.setChipStrokeWidth(dp(1));
        chip.setChipStartPadding(dp(8));
        chip.setChipEndPadding(dp(8));
        chip.setTextStartPadding(0);
        chip.setTextEndPadding(0);
        chip.setEnsureMinTouchTargetSize(false);
        chip.setCheckable(false);
        chip.setClickable(false);
        chip.setFocusable(false);
        chip.setMinHeight(dp(28));
        return chip;
    }

    private MaterialButton primaryButton(String value, @DrawableRes int iconResource) {
        MaterialButton button = new MaterialButton(this);
        styleButton(button, value, iconResource);
        button.setBackgroundTintList(Tints.primaryButtonBackground());
        button.setTextColor(Tints.primaryButtonText());
        button.setIconTint(Tints.primaryButtonText());
        button.setStrokeWidth(0);
        return button;
    }

    private MaterialButton secondaryButton(String value, @DrawableRes int iconResource) {
        MaterialButton button = new MaterialButton(this);
        styleButton(button, value, iconResource);
        button.setBackgroundTintList(Tints.secondaryButtonBackground());
        button.setTextColor(Tints.secondaryButtonText());
        button.setIconTint(Tints.secondaryButtonText());
        button.setStrokeColor(ColorStateList.valueOf(Tints.BORDER_STRONG));
        button.setStrokeWidth(dp(1));
        return button;
    }

    private void styleButton(MaterialButton button, String value, @DrawableRes int iconResource) {
        button.setText(value);
        button.setAllCaps(false);
        button.setTextSize(13);
        button.setSingleLine(true);
        button.setMaxLines(1);
        TextViewCompat.setAutoSizeTextTypeUniformWithConfiguration(
                button, 11, 13, 1, android.util.TypedValue.COMPLEX_UNIT_SP);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        button.setMinHeight(dp(48));
        button.setMinimumHeight(dp(48));
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setInsetTop(0);
        button.setInsetBottom(0);
        button.setCornerRadius(dp(12));
        button.setRippleColor(Tints.ripple());
        button.setPadding(dp(12), 0, dp(12), 0);
        if (iconResource != 0) {
            button.setIconResource(iconResource);
            button.setIconTint(Tints.iconTint(Tints.TEXT));
            button.setIconSize(dp(16));
            button.setIconPadding(dp(5));
            button.setIconGravity(MaterialButton.ICON_GRAVITY_TEXT_START);
        }
    }

    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setTypeface(Typeface.create(bold ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
        return view;
    }

    private void addTop(LinearLayout parent, View child, int topDp) {
        parent.addView(child, spacedRow(topDp));
    }

    private LinearLayout.LayoutParams row() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams spacedRow(int topDp) {
        LinearLayout.LayoutParams params = row();
        params.topMargin = dp(topDp);
        return params;
    }

    private LinearLayout.LayoutParams wrapEnd() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.gravity = Gravity.CENTER_VERTICAL;
        return params;
    }

    private int adaptiveGutter() {
        int width = getResources().getConfiguration().screenWidthDp;
        int height = getResources().getConfiguration().screenHeightDp;
        if (width >= 840) return dp(96);
        if (width >= 600) return dp(56);
        if (width > height) return dp(32);
        return dp(20);
    }

    private int challengeHeight() {
        int screenHeight = getResources().getConfiguration().screenHeightDp;
        return dp(Math.max(300, Math.min(520, Math.round(screenHeight * 0.58f))));
    }

    private int navigationHeight() {
        return dp(isLandscape() ? 56 : 64);
    }

    private void claimRelayPairing(android.net.Uri uri) {
        final String hub = uri.getQueryParameter("hub");
        final String mailbox = uri.getQueryParameter("mailbox");
        final String join = uri.getQueryParameter("join");
        final String encodedSecret = uri.getQueryParameter("secret");
        final String nodeName = uri.getQueryParameter("name");
        if (hub == null || mailbox == null || join == null || encodedSecret == null
                || nodeName == null || !"1".equals(uri.getQueryParameter("v"))
                || !RelayStore.validHub(hub)) {
            Toast.makeText(this, "配对二维码无效", Toast.LENGTH_LONG).show();
            return;
        }
        final byte[] pairSecret;
        try {
            pairSecret = RelayCrypto.decode(encodedSecret);
            if (pairSecret.length != 32) throw new IllegalArgumentException("secret");
        } catch (Exception exception) {
            Toast.makeText(this, "配对二维码无效", Toast.LENGTH_LONG).show();
            return;
        }
        if (relayStatus != null) relayStatus.setText("个人 Agent：正在建立加密配对…");
        executor.submit(() -> {
            try {
                JSONObject body = new JSONObject().put("joinToken", join)
                        .put("phoneName", Build.MANUFACTURER + " " + Build.MODEL);
                JSONObject claimed = new JSONObject(Http.post(http,
                        hub.replaceAll("/+$", "") + "/v1/pairing/claim",
                        body.toString(), "").body);
                if (!mailbox.equals(claimed.getString("mailboxId"))) {
                    throw new IllegalArgumentException("mailbox mismatch");
                }
                RelayStore.save(this, new JSONObject().put("hub", hub)
                        .put("mailboxId", mailbox)
                        .put("deviceToken", claimed.getString("deviceToken"))
                        .put("pairSecret", RelayCrypto.encode(pairSecret))
                        .put("nodeName", nodeName));
                runOnUiThread(() -> {
                    if (relayStatus != null) {
                        relayStatus.setText("个人 Agent：已端到端配对 · " + nodeName);
                        relayStatus.setTextColor(Tints.ACCENT);
                    }
                    Toast.makeText(this, "配对成功；Hub 无法读取任务内容", Toast.LENGTH_LONG).show();
                    startRelayNotifications();
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    if (relayStatus != null) relayStatus.setText("个人 Agent：配对失败，请重新扫码");
                    Toast.makeText(this, "配对失败：二维码可能已过期", Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private void startRelayNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            pendingRelayPermission = true;
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    REQUEST_NOTIFICATIONS);
            return;
        }
        RelayWatchService.start(this);
    }

    private void processPendingRelay() {
        if (relayProcessing || destroyed || !foregroundVisible) return;
        String stored = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(RelayStore.PREF_PENDING, "");
        RelayStore.Config config = RelayStore.load(this);
        if (stored.isEmpty() || config == null) return;
        relayProcessing = true;
        selectPage(R.id.nav_task);
        executor.submit(() -> solveRelayEnvelope(config, stored));
    }

    private void solveRelayEnvelope(RelayStore.Config config, String stored) {
        JSONObject inputEnvelope = null;
        JSONObject taskPayload = null;
        boolean delivered = false;
        try {
            inputEnvelope = new JSONObject(stored);
            taskPayload = RelayCrypto.decrypt(config.secret, inputEnvelope, "node_to_phone");
            if (!"captcha_task".equals(taskPayload.getString("kind"))) {
                throw new IllegalArgumentException("unknown relay payload");
            }
            CaptchaTask task = new CaptchaTask(taskPayload);
            JSONObject inline = taskPayload.optJSONObject("assets");
            Map<String, Bitmap> assets = new HashMap<>();
            for (String name : task.assetNames()) {
                JSONObject asset = inline == null ? null : inline.optJSONObject(task.assetId(name));
                if (asset == null) throw new IllegalArgumentException("missing inline asset");
                byte[] bytes = Base64.decode(asset.getString("data"), Base64.DEFAULT);
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                if (bitmap == null) throw new IllegalArgumentException("invalid inline asset");
                assets.put(name, bitmap);
            }
            runOnUiThread(() -> {
                setRunState("待验证", friendlyCaptcha(task.type) + " · 来自已配对的个人 Agent",
                        Tints.WARNING, Tints.WARNING_SOFT);
                appendLogOnUi("收到端到端加密的 " + task.type + " 任务");
            });
            Solver.Solution solved = solver.solve(task, assets);
            sendRelayResult(config, new JSONObject().put("kind", "captcha_result")
                    .put("taskId", task.id).put("status", "ready")
                    .put("solution", solved.value));
            ackRelay(config, inputEnvelope.getString("messageId"));
            delivered = true;
            runOnUiThread(() -> appendLogOnUi("加密结果已回传给个人 Agent"));
        } catch (Exception exception) {
            try {
                if (taskPayload != null && inputEnvelope != null) {
                    String taskId = taskPayload.optString("taskId", "unknown");
                    sendRelayResult(config, new JSONObject().put("kind", "captcha_result")
                            .put("taskId", taskId).put("status", "failed")
                            .put("errorDescription", concise(exception)));
                    ackRelay(config, inputEnvelope.getString("messageId"));
                    delivered = true;
                }
            } catch (Exception ignored) { }
            runOnUiThread(() -> Toast.makeText(this,
                    "加密任务处理失败：" + concise(exception), Toast.LENGTH_LONG).show());
        } finally {
            if (delivered) {
                getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                        .remove(RelayStore.PREF_PENDING).apply();
            }
            relayProcessing = false;
        }
    }

    private void sendRelayResult(RelayStore.Config config, JSONObject result) throws Exception {
        JSONObject envelope = RelayCrypto.encrypt(
                config.secret, config.mailbox, "phone_to_node", result);
        Http.post(http, config.hub + "/v1/relay/messages", envelope.toString(),
                "Device " + config.token);
    }

    private void ackRelay(RelayStore.Config config, String messageId) throws Exception {
        Http.post(http, config.hub + "/v1/relay/ack",
                new JSONObject().put("messageId", messageId).toString(),
                "Device " + config.token);
    }

    private boolean isLandscape() {
        return getResources().getConfiguration().screenWidthDp
                > getResources().getConfiguration().screenHeightDp;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        state.putInt(STATE_SELECTED_PAGE, selectedPageId);
        super.onSaveInstanceState(state);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        active = false;
        solver.shutdown();
        executor.shutdownNow();
        diagnosticExecutor.shutdownNow();
        super.onDestroy();
    }
}
