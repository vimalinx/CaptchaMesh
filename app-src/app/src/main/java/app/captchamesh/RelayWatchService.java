package app.captchamesh;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;

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
    private static final int SERVICE_ID = 4201;
    private static final int CHALLENGE_ID = 4202;
    private static final String SERVICE_CHANNEL = "relay_waiting";
    private static final String CHALLENGE_CHANNEL = "relay_captcha_ready";
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .build();
    private volatile boolean stopped;
    private volatile boolean watching;

    static void start(Context context) {
        ContextCompat.startForegroundService(context, new Intent(context, RelayWatchService.class));
    }

    @Override public void onCreate() {
        super.onCreate();
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
            stopSelf();
            return START_NOT_STICKY;
        }
        stopped = false;
        startForeground(SERVICE_ID, waiting("已加密连接，后台等待任务"));
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
                RelayStore.Config config = RelayStore.load(this);
                if (config == null) break;
                if (!getSharedPreferences("cm", MODE_PRIVATE)
                        .getString(RelayStore.PREF_PENDING, "").isEmpty()) {
                    sleep(1500);
                    continue;
                }
                Http.Result result = Http.post(http, config.hub + "/v1/relay/poll",
                        new JSONObject().put("waitSeconds", 15).toString(),
                        "Device " + config.token);
                failures = 0;
                if (result.code == 204 || result.body.isEmpty()) continue;
                JSONObject envelope = new JSONObject(result.body);
                if (!config.mailbox.equals(envelope.getString("mailboxId"))
                        || !"node_to_phone".equals(envelope.getString("direction"))) {
                    throw new IllegalArgumentException("unexpected relay message");
                }
                // This is still endpoint-encrypted ciphertext; plaintext is only opened in the UI.
                getSharedPreferences("cm", MODE_PRIVATE).edit()
                        .putString(RelayStore.PREF_PENDING, envelope.toString()).apply();
                notifyReady();
                failures = 0;
            } catch (Exception exception) {
                failures++;
                getSystemService(NotificationManager.class).notify(
                        SERVICE_ID, waiting("连接暂时中断，后台重试中"));
                sleep(Math.min(10_000, 1000L * failures));
            }
        }
        watching = false;
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

    private void notifyReady() {
        if (!NotificationPreferences.taskAlertsEnabled(this)) return;
        getSystemService(NotificationManager.class).notify(CHALLENGE_ID,
                new NotificationCompat.Builder(this, CHALLENGE_CHANNEL)
                        .setSmallIcon(R.drawable.ic_notification)
                        .setColor(Tints.ACCENT)
                        .setContentTitle("个人 Agent 需要你验证")
                        .setContentText("点此在手机上手动完成")
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
        stopped = true;
        executor.shutdownNow();
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
}
