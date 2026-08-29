package app.captchamesh;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.format.DateFormat;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class DiagnosticLog {
    private static final String PREFERENCES = "cm";
    private static final String CIPHERTEXT = "diagnostic_log_ciphertext";
    private static final String IV = "diagnostic_log_iv";
    private static final String KEYSTORE_ALIAS = "captchamesh_diagnostic_log_v1";
    private static final int MAX_STORED_CHARACTERS = 16_000;
    private static final int MAX_STACK_FRAMES = 12;

    private DiagnosticLog() { }

    static synchronized void event(Context context, String component, String event) {
        append(context, safeLabel(component) + "/" + safeLabel(event));
    }

    static synchronized void error(
            Context context, String component, String event, Throwable throwable) {
        StringBuilder entry = new StringBuilder()
                .append(safeLabel(component)).append('/').append(safeLabel(event));
        if (throwable != null) {
            entry.append(" type=").append(throwable.getClass().getName());
            int included = 0;
            for (StackTraceElement frame : throwable.getStackTrace()) {
                if (!frame.getClassName().startsWith("app.captchamesh.")) continue;
                entry.append("\n  at ")
                        .append(frame.getClassName()).append('.')
                        .append(frame.getMethodName()).append('(')
                        .append(frame.getFileName() == null ? "Unknown" : frame.getFileName())
                        .append(':').append(Math.max(frame.getLineNumber(), 0)).append(')');
                included++;
                if (included >= MAX_STACK_FRAMES) break;
            }
        }
        append(context, entry.toString());
    }

    static synchronized String report(Context context) {
        String version = "unknown";
        try {
            PackageInfo info = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0);
            version = info.versionName == null ? "unknown" : info.versionName;
        } catch (Exception ignored) { }
        String records = read(context);
        return "CaptchaMesh diagnostic\n"
                + "app=" + version + "\n"
                + "android=" + Build.VERSION.SDK_INT + "\n"
                + "device=" + safeLabel(Build.MANUFACTURER) + "/" + safeLabel(Build.MODEL) + "\n"
                + "privacy=exception-type-and-app-stack-only\n\n"
                + (records.isEmpty() ? "No diagnostic errors recorded." : records);
    }

    static synchronized void clear(Context context) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
                .remove(CIPHERTEXT)
                .remove(IV)
                .apply();
    }

    private static void append(Context context, String value) {
        String time = DateFormat.format("yyyy-MM-dd HH:mm:ss", System.currentTimeMillis())
                .toString();
        String existing = read(context);
        String updated = "[" + time + "] " + value
                + (existing.isEmpty() ? "" : "\n\n" + existing);
        if (updated.length() > MAX_STORED_CHARACTERS) {
            updated = updated.substring(0, MAX_STORED_CHARACTERS);
        }
        write(context, updated);
    }

    private static String safeLabel(String value) {
        if (value == null || value.isEmpty()) return "unknown";
        String sanitized = value.replaceAll("[^A-Za-z0-9_.-]", "_");
        return sanitized.substring(0, Math.min(sanitized.length(), 80));
    }

    private static String read(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(
                PREFERENCES, Context.MODE_PRIVATE);
        String ciphertext = preferences.getString(CIPHERTEXT, "");
        String iv = preferences.getString(IV, "");
        if (ciphertext.isEmpty() || iv.isEmpty()) return "";
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(
                    128, Base64.decode(iv, Base64.NO_WRAP)));
            return new String(cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)),
                    StandardCharsets.UTF_8);
        } catch (Exception exception) {
            preferences.edit().remove(CIPHERTEXT).remove(IV).apply();
            return "";
        }
    }

    private static void write(Context context, String value) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] ciphertext = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
                    .putString(CIPHERTEXT, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
                    .putString(IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                    .apply();
        } catch (Exception ignored) {
            // Diagnostic logging must never crash or block the app.
        }
    }

    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        SecretKey existing = (SecretKey) store.getKey(KEYSTORE_ALIAS, null);
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
}
