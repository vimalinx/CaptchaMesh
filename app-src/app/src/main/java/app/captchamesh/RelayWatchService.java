package app.captchamesh;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;

public final class RelayWatchService extends Service {
    static final String ACTION_OPEN_RELAY = "app.captchamesh.action.OPEN_RELAY";
    static final String ACTION_QUEUE_CHANGED = "app.captchamesh.action.RELAY_QUEUE_CHANGED";
    private static final int SERVICE_ID = 4201;
    private static final int CHALLENGE_ID = 4202;
    private static final String SERVICE_CHANNEL = "relay_waiting";
    private static final String CHALLENGE_CHANNEL = "relay_captcha_ready";
    private static final long WAKE_LOCK_TIMEOUT_MS = 10 * 60_000L;
    private static final long WAKE_LOCK_REFRESH_MS = 8 * 60_000L;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .build();
    private volatile boolean stopped;
    private volatile boolean watching;
    private PowerManager.WakeLock wakeLock;
    private long wakeLockAcquiredAt;

    static void start(Context context) {
        ContextCompat.startForegroundService(context, new Intent(context, RelayWatchService.class));
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, RelayWatchService.class));
    }

    @Override public void onCreate() {
        super.onCreate();
        PowerManager power = getSystemService(PowerManager.class);
        if (power != null) {
            wakeLock = power.newWakeLock(
                    PowerManager.PARTIAL_WAKE_LOCK, getPackageName() + ":RelayWatch");
            wakeLock.setReferenceCounted(false);
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel waiting = new NotificationChannel(
                SERVICE_CHANNEL, "个人 Agent 后台连接", NotificationManager.IMPORTANCE_LOW);
        waiting.setDescription("保持与已配对电脑的端到端加密连接");
        waiting.setShowBadge(false);
        manager.createNotificationChannel(waiting);
        NotificationChannel ready = new NotificationChannel(
                CHALLENGE_CHANNEL, "加密任务到达提醒", NotificationManager.IMPORTANCE_HIGH);
        ready.setDescription("已配对的个人 Agent 有人工验证任务时提醒");
        manager.createNotificationChannel(ready);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        RelayStore.Config config = RelayStore.load(this);
        if (config == null) {
            DiagnosticLog.event(this, "RELAY", "CONFIG_MISSING");
            stopSelf();
            return START_NOT_STICKY;
        }
        DiagnosticLog.event(this, "RELAY", "WATCH_STARTED");
        stopped = false;
        startForeground(SERVICE_ID, waiting(backgroundStatusText()));
        ensureWakeLock();
        if (!watching) {
            watching = true;
            executor.submit(this::watch);
        }
        return START_STICKY;
    }

    private void watch() {
        int failures = 0;
        while (!stopped && !Thread.currentThread().isInterrupted()) {
            try {
                ensureWakeLock();
                RelayStore.Config config = RelayStore.load(this);
                if (config == null) break;
                Http.Result result = Http.post(http, config.hub + "/v1/relay/poll",
                        new JSONObject().put("waitSeconds", 15).toString(),
                        "Device " + config.token);
                if (failures > 0) DiagnosticLog.event(this, "RELAY", "CONNECTION_RECOVERED");
                failures = 0;
                if (result.code == 204 || result.body.isEmpty()) continue;
                JSONObject envelope = new JSONObject(result.body);
                if (!config.mailbox.equals(envelope.getString("mailboxId"))
                        || !"node_to_phone".equals(envelope.getString("direction"))) {
                    throw new IllegalArgumentException("unexpected relay message");
                }
                // The queue contains endpoint-encrypted ciphertext. ACK only after durable local storage.
                RelayStore.EnqueueResult queued = RelayStore.enqueueEnvelope(this, envelope);
                if (queued == RelayStore.EnqueueResult.FULL) {
                    DiagnosticLog.event(this, "RELAY", "QUEUE_FULL");
                    getSystemService(NotificationManager.class).notify(
                            SERVICE_ID, waiting("手机任务队列已满，等待你处理"));
                    sleep(1500);
                    continue;
                }
                Http.post(http, config.hub + "/v1/relay/ack",
                        new JSONObject().put("messageId", envelope.getString("messageId")).toString(),
                        "Device " + config.token);
                if (queued == RelayStore.EnqueueResult.ENQUEUED) {
                    int pending = RelayStore.pendingCount(this);
                    notifyReady(pending);
                    sendBroadcast(new Intent(ACTION_QUEUE_CHANGED).setPackage(getPackageName()));
                    MainActivity.notifyRelayQueueChanged();
                }
                failures = 0;
            } catch (Exception exception) {
                failures++;
                if (failures == 1 || failures % 6 == 0) {
                    DiagnosticLog.error(this, "RELAY", "POLL_FAILED", exception);
                }
                getSystemService(NotificationManager.class).notify(
                        SERVICE_ID, waiting("连接暂时中断，后台重试中"));
                sleep(Math.min(10_000, 1000L * failures));
            }
        }
        watching = false;
    }

    private void ensureWakeLock() {
        if (wakeLock == null) return;
        long now = SystemClock.elapsedRealtime();
        if (wakeLock.isHeld() && now - wakeLockAcquiredAt < WAKE_LOCK_REFRESH_MS) return;
        if (wakeLock.isHeld()) wakeLock.release();
        wakeLock.acquire(WAKE_LOCK_TIMEOUT_MS);
        wakeLockAcquiredAt = now;
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLockAcquiredAt = 0;
    }

    private android.app.Notification waiting(String text) {
        return new NotificationCompat.Builder(this, SERVICE_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(Tints.ACCENT)
                .setContentTitle("CaptchaMesh 已配对")
                .setContentText(text)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setOngoing(true).setOnlyAlertOnce(true)
                .setContentIntent(openIntent()).build();
    }

    private String backgroundStatusText() {
        PowerManager power = getSystemService(PowerManager.class);
        if (power != null && power.isIgnoringBatteryOptimizations(getPackageName())) {
            if ("HONOR".equalsIgnoreCase(android.os.Build.MANUFACTURER)) {
                return "已加密连接；请在荣耀管家确认应用启动管理";
            }
            return "已加密连接，后台等待任务";
        }
        return "已加密连接；请在 App 设置中开启后台保护";
    }

    private void notifyReady(int pending) {
        if (!NotificationPreferences.taskAlertsEnabled(this)) return;
        getSystemService(NotificationManager.class).notify(CHALLENGE_ID,
                new NotificationCompat.Builder(this, CHALLENGE_CHANNEL)
                        .setSmallIcon(R.drawable.ic_notification)
                        .setColor(Tints.ACCENT)
                        .setContentTitle("个人 Agent 需要你验证")
                        .setContentText(pending + " 个 Agent 任务等待处理")
                        .setPriority(NotificationCompat.PRIORITY_HIGH)
                        .setCategory(NotificationCompat.CATEGORY_ALARM)
                        .setAutoCancel(true)
                        .setContentIntent(openIntent()).build());
    }

    private PendingIntent openIntent() {
        Intent intent = new Intent(this, MainActivity.class)
                .setAction(ACTION_OPEN_RELAY)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(this, 4202, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void sleep(long milliseconds) {
        try { Thread.sleep(milliseconds); }
        catch (InterruptedException exception) { Thread.currentThread().interrupt(); }
    }

    @Nullable @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        DiagnosticLog.event(this, "RELAY", "WATCH_STOPPED");
        stopped = true;
        executor.shutdownNow();
        releaseWakeLock();
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
}
