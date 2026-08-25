package app.captchamesh;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.IBinder;
import android.util.Base64;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

import okhttp3.OkHttpClient;

public final class CaptchaWatchService extends Service {
    static final String ACTION_START = "app.captchamesh.action.WATCH_RUN";
    static final String ACTION_STOP = "app.captchamesh.action.STOP_WATCH";
    static final String ACTION_OPEN_CHALLENGE = "app.captchamesh.action.OPEN_CHALLENGE";
    static final String EXTRA_RUN_ID = "run_id";
    static final String EXTRA_RUN_NAME = "run_name";
    static final String PREF_ACTIVE_RUN_ID = "active_run_id";
    static final String PREF_ACTIVE_RUN_NAME = "active_run_name";

    private static final String PREFERENCES = "cm";
    private static final String API_KEY_CIPHERTEXT = "api_key_ciphertext";
    private static final String API_KEY_IV = "api_key_iv";
    private static final String KEYSTORE_ALIAS = "captchamesh_api_key_v1";
    private static final int GCM_TAG_LENGTH_BITS = 128;
    private static final int WAITING_NOTIFICATION_ID = 4101;
    private static final int CHALLENGE_NOTIFICATION_ID = 4102;
    private static final String WAITING_CHANNEL = "registration_waiting";
    private static final String CHALLENGE_CHANNEL = "captcha_ready";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .build();
    private volatile String watchedRunId = "";
    private volatile boolean stopped;

    static void start(Context context, String runId, String name) {
        Intent intent = new Intent(context, CaptchaWatchService.class)
                .setAction(ACTION_START)
                .putExtra(EXTRA_RUN_ID, runId)
                .putExtra(EXTRA_RUN_NAME, name);
        ContextCompat.startForegroundService(context, intent);
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, CaptchaWatchService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        if (ACTION_STOP.equals(intent.getAction())) {
            String runId = intent.getStringExtra(EXTRA_RUN_ID);
            String name = intent.getStringExtra(EXTRA_RUN_NAME);
            if (runId == null || runId.isEmpty()) {
                stopSelf();
                return START_NOT_STICKY;
            }
            if (name == null || name.isEmpty()) name = "电脑工作流";
            watchedRunId = runId;
            stopped = true;
            getSystemService(NotificationManager.class).cancel(CHALLENGE_NOTIFICATION_ID);
            startForeground(WAITING_NOTIFICATION_ID, waitingNotification(name, "正在请求电脑停止任务"));
            String finalName = name;
            executor.submit(() -> requestStop(runId, finalName));
            return START_NOT_STICKY;
        }
        String runId = intent.getStringExtra(EXTRA_RUN_ID);
        String name = intent.getStringExtra(EXTRA_RUN_NAME);
        if (runId == null || runId.isEmpty()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (name == null || name.isEmpty()) name = "电脑工作流";
        watchedRunId = runId;
        stopped = false;
        startForeground(WAITING_NOTIFICATION_ID, waitingNotification(
                name, "后台等待 CAPTCHA；可放心切换应用"));
        String finalName = name;
        executor.submit(() -> watch(runId, finalName));
        return START_REDELIVER_INTENT;
    }

    private void watch(String runId, String name) {
        boolean challengeNotified = false;
        int failures = 0;
        while (!stopped && runId.equals(watchedRunId) && !Thread.currentThread().isInterrupted()) {
            try {
                SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
                String baseUrl = preferences.getString("broker", "https://mesh.vimalinx.com")
                        .replaceAll("/+$", "");
                String apiKey = loadApiKey(preferences);
                String authorization = apiKey.isEmpty() ? "" : "Bearer " + apiKey;
                JSONObject response = new JSONObject(Http.get(
                        http,
                        baseUrl + "/v1/runs/" + android.net.Uri.encode(runId),
                        authorization).body);
                String status = response.getJSONObject("run").optString("status", "running");
                if (terminal(status)) {
                    clearStoredRun(runId);
                    stopWatching(false);
                    return;
                }
                JSONObject tasks = response.optJSONObject("tasks");
                int pending = tasks == null ? 0 : tasks.optInt("pending", 0);
                int leased = tasks == null ? 0 : tasks.optInt("leased", 0);
                boolean ready = pending + leased > 0;
                if (ready && !challengeNotified && !MainActivity.foregroundVisible) {
                    challengeNotified = true;
                    notifyChallenge(runId, name);
                }
                if (ready) {
                    updateWaiting(name, "CAPTCHA 已到达，点通知进入任务页");
                } else if (!ready) {
                    challengeNotified = false;
                    updateWaiting(name, "后台等待 CAPTCHA；可放心切换应用");
                }
                failures = 0;
                sleep(ready ? 1000 : 2000);
            } catch (Exception exception) {
                failures++;
                if (failures == 1 || failures % 6 == 0) {
                    updateWaiting(name, "连接暂时中断，后台仍在重试");
                }
                sleep(Math.min(10_000, 1500L * failures));
            }
        }
    }

    private void notifyChallenge(String runId, String name) {
        if (!NotificationPreferences.taskAlertsEnabled(this)) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.notify(CHALLENGE_NOTIFICATION_ID, new NotificationCompat.Builder(this, CHALLENGE_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(Tints.ACCENT)
                .setContentTitle(name + " 需要你验证")
                .setContentText("CAPTCHA 已到达，点此在手机完成")
                .setStyle(new NotificationCompat.BigTextStyle()
                        .bigText("CAPTCHA 已到达。点击通知返回 CaptchaMesh 手动完成；不会自动代答。"))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setAutoCancel(true)
                .setContentIntent(openIntent(runId, name))
                .addAction(R.drawable.ic_shield, "立即处理", openIntent(runId, name))
                .addAction(R.drawable.ic_stop, "停止任务", stopIntent(runId, name))
                .build());
    }

    private Notification waitingNotification(String name, String message) {
        return new NotificationCompat.Builder(this, WAITING_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(Tints.ACCENT)
                .setContentTitle(name + " 正在运行")
                .setContentText(message)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openIntent(watchedRunId, name))
                .addAction(R.drawable.ic_shield, "立即处理", openIntent(watchedRunId, name))
                .addAction(R.drawable.ic_stop, "停止任务", stopIntent(watchedRunId, name))
                .build();
    }

    private void updateWaiting(String name, String message) {
        getSystemService(NotificationManager.class).notify(
                WAITING_NOTIFICATION_ID, waitingNotification(name, message));
    }

    private PendingIntent openIntent(String runId, String name) {
        Intent intent = new Intent(this, MainActivity.class)
                .setAction(ACTION_OPEN_CHALLENGE)
                .putExtra(EXTRA_RUN_ID, runId)
                .putExtra(EXTRA_RUN_NAME, name)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(
                this,
                runId == null ? 0 : runId.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent stopIntent(String runId, String name) {
        Intent intent = new Intent(this, CaptchaWatchService.class)
                .setAction(ACTION_STOP)
                .putExtra(EXTRA_RUN_ID, runId)
                .putExtra(EXTRA_RUN_NAME, name);
        return PendingIntent.getForegroundService(
                this,
                runId == null ? 1 : runId.hashCode() + 1,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void requestStop(String runId, String name) {
        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        String storedRunId = preferences.getString(PREF_ACTIVE_RUN_ID, "");
        if (!storedRunId.isEmpty() && !storedRunId.equals(runId)) {
            updateWaiting(name, "停止请求已忽略：当前任务已经变化");
            stopped = false;
            executor.submit(() -> watch(storedRunId,
                    preferences.getString(PREF_ACTIVE_RUN_NAME, "电脑工作流")));
            return;
        }
        try {
            String baseUrl = preferences.getString("broker", "https://mesh.vimalinx.com")
                    .replaceAll("/+$", "");
            String apiKey = loadApiKey(preferences);
            String authorization = apiKey.isEmpty() ? "" : "Bearer " + apiKey;
            Http.post(
                    http,
                    baseUrl + "/v1/runs/" + android.net.Uri.encode(runId) + "/stop",
                    "{}",
                    authorization);
            clearStoredRun(runId);
            stopWatching(false);
        } catch (Exception exception) {
            if (runIsTerminal(preferences, runId)) {
                clearStoredRun(runId);
                stopWatching(false);
                return;
            }
            stopped = false;
            updateWaiting(name, "停止请求失败，后台仍在监视；请稍后重试");
            executor.submit(() -> watch(runId, name));
        }
    }

    private boolean runIsTerminal(SharedPreferences preferences, String runId) {
        try {
            String baseUrl = preferences.getString("broker", "https://mesh.vimalinx.com")
                    .replaceAll("/+$", "");
            String apiKey = loadApiKey(preferences);
            String authorization = apiKey.isEmpty() ? "" : "Bearer " + apiKey;
            JSONObject response = new JSONObject(Http.get(
                    http,
                    baseUrl + "/v1/runs/" + android.net.Uri.encode(runId),
                    authorization).body);
            return terminal(response.getJSONObject("run").optString("status"));
        } catch (Exception ignored) {
            return false;
        }
    }

    private void createChannels() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel waiting = new NotificationChannel(
                WAITING_CHANNEL, "工作流后台运行", NotificationManager.IMPORTANCE_LOW);
        waiting.setDescription("显示你主动启动的电脑工作流正在等待 CAPTCHA");
        waiting.setShowBadge(false);
        manager.createNotificationChannel(waiting);

        NotificationChannel challenge = new NotificationChannel(
                CHALLENGE_CHANNEL, "CAPTCHA 到达提醒", NotificationManager.IMPORTANCE_HIGH);
        challenge.setDescription("当人工 CAPTCHA 到达手机时提醒你处理");
        challenge.enableVibration(true);
        challenge.setLightColor(Color.GREEN);
        manager.createNotificationChannel(challenge);
    }

    private String loadApiKey(SharedPreferences preferences) {
        String ciphertext = preferences.getString(API_KEY_CIPHERTEXT, "");
        String iv = preferences.getString(API_KEY_IV, "");
        if (ciphertext.isEmpty() || iv.isEmpty()) return "";
        try {
            KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
            keyStore.load(null);
            SecretKey key = (SecretKey) keyStore.getKey(KEYSTORE_ALIAS, null);
            if (key == null) return "";
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(
                    GCM_TAG_LENGTH_BITS, Base64.decode(iv, Base64.NO_WRAP)));
            return new String(cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)),
                    StandardCharsets.UTF_8);
        } catch (Exception ignored) {
            return "";
        }
    }

    private void clearStoredRun(String runId) {
        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        if (!runId.equals(preferences.getString(PREF_ACTIVE_RUN_ID, ""))) return;
        preferences.edit()
                .remove(PREF_ACTIVE_RUN_ID)
                .remove(PREF_ACTIVE_RUN_NAME)
                .apply();
    }

    private void stopWatching(boolean clearStored) {
        String runId = watchedRunId;
        stopped = true;
        watchedRunId = "";
        if (clearStored && !runId.isEmpty()) clearStoredRun(runId);
        getSystemService(NotificationManager.class).cancel(CHALLENGE_NOTIFICATION_ID);
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private boolean terminal(String status) {
        return status.equals("succeeded") || status.equals("failed")
                || status.equals("cancelled") || status.equals("interrupted");
    }

    private void sleep(long milliseconds) {
        try {
            Thread.sleep(milliseconds);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopped = true;
        executor.shutdownNow();
        getSystemService(NotificationManager.class).cancel(CHALLENGE_NOTIFICATION_ID);
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
}
