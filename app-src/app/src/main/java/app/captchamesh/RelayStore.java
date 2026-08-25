package app.captchamesh;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.net.URI;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class RelayStore {
    static final String PREF_PENDING = "relay_pending_envelope";
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
            preferences.edit().remove(DATA).remove(IV).remove(PREF_PENDING).apply();
            return null;
        }
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
