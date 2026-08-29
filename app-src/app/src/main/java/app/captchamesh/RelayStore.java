package app.captchamesh;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONObject;
import org.json.JSONArray;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.net.URI;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class RelayStore {
    enum EnqueueResult { ENQUEUED, DUPLICATE, FULL }

    static final String PREF_PENDING = "relay_pending_envelope";
    static final String PREF_PENDING_QUEUE = "relay_pending_envelopes_v2";
    static final int MAX_PENDING_ENVELOPES = 32;
    private static final String PREFS = "cm";
    private static final String DATA = "relay_config_ciphertext";
    private static final String IV = "relay_config_iv";
    private static final String ALIAS = "captchamesh_relay_config_v1";

    static final class Config {
        final String hub;
        final String mailbox;
        final String token;
        final byte[] secret;
        final String nodeName;

        Config(JSONObject value) throws Exception {
            hub = value.getString("hub").replaceAll("/+$", "");
            mailbox = value.getString("mailboxId");
            token = value.getString("deviceToken");
            secret = RelayCrypto.decode(value.getString("pairSecret"));
            nodeName = value.optString("nodeName", "个人 Agent");
            if (secret.length != 32 || !validHub(hub)) {
                throw new IllegalArgumentException("invalid relay configuration");
            }
        }
    }

    static boolean validHub(String value) {
        try {
            URI uri = new URI(value);
            if (uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null
                    || uri.getFragment() != null) return false;
            if ("https".equalsIgnoreCase(uri.getScheme())) return true;
            return "http".equalsIgnoreCase(uri.getScheme())
                    && "127.0.0.1".equals(uri.getHost());
        } catch (Exception exception) {
            return false;
        }
    }

    static void save(Context context, JSONObject value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(value.toString().getBytes(StandardCharsets.UTF_8));
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(DATA, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .putString(IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .remove(PREF_PENDING)
                .remove(PREF_PENDING_QUEUE)
                .apply();
    }

    static Config load(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String data = preferences.getString(DATA, "");
        String iv = preferences.getString(IV, "");
        if (data.isEmpty() || iv.isEmpty()) return null;
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(),
                    new GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)));
            return new Config(new JSONObject(new String(cipher.doFinal(
                    Base64.decode(data, Base64.NO_WRAP)), StandardCharsets.UTF_8)));
        } catch (Exception exception) {
            preferences.edit().remove(DATA).remove(IV).remove(PREF_PENDING)
                    .remove(PREF_PENDING_QUEUE).apply();
            return null;
        }
    }

    static synchronized EnqueueResult enqueueEnvelope(Context context, JSONObject envelope) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        JSONArray queue = readQueue(preferences);
        String messageId = envelope.optString("messageId", "");
        if (messageId.isEmpty()) throw new IllegalArgumentException("relay message id missing");
        for (int index = 0; index < queue.length(); index++) {
            JSONObject queued = queue.optJSONObject(index);
            if (queued != null && messageId.equals(queued.optString("messageId"))) {
                return EnqueueResult.DUPLICATE;
            }
        }
        if (queue.length() >= MAX_PENDING_ENVELOPES) return EnqueueResult.FULL;
        queue.put(envelope);
        if (!preferences.edit().putString(PREF_PENDING_QUEUE, queue.toString())
                .remove(PREF_PENDING).commit()) {
            throw new IllegalStateException("could not persist relay queue");
        }
        return EnqueueResult.ENQUEUED;
    }

    static synchronized JSONArray pendingEnvelopes(Context context) {
        return readQueue(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE));
    }

    static synchronized JSONObject peekEnvelope(Context context) {
        return pendingEnvelopes(context).optJSONObject(0);
    }

    static synchronized boolean removeEnvelope(Context context, String messageId) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        JSONArray queue = readQueue(preferences);
        JSONArray remaining = new JSONArray();
        boolean removed = false;
        for (int index = 0; index < queue.length(); index++) {
            JSONObject queued = queue.optJSONObject(index);
            if (!removed && queued != null && messageId.equals(queued.optString("messageId"))) {
                removed = true;
            } else if (queued != null) {
                remaining.put(queued);
            }
        }
        if (!removed) return false;
        return preferences.edit().putString(PREF_PENDING_QUEUE, remaining.toString())
                .remove(PREF_PENDING).commit();
    }

    static synchronized int pendingCount(Context context) {
        return pendingEnvelopes(context).length();
    }

    private static JSONArray readQueue(SharedPreferences preferences) {
        JSONArray queue;
        try {
            queue = new JSONArray(preferences.getString(PREF_PENDING_QUEUE, "[]"));
        } catch (Exception exception) {
            queue = new JSONArray();
        }
        String legacy = preferences.getString(PREF_PENDING, "");
        if (queue.length() == 0 && !legacy.isEmpty()) {
            try { queue.put(new JSONObject(legacy)); }
            catch (Exception ignored) { }
        }
        return queue;
    }

    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        SecretKey existing = (SecretKey) store.getKey(ALIAS, null);
        if (existing != null) return existing;
        KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }
}
