package app.captchamesh;

import org.json.JSONObject;

/** Executes a Python-produced AES-GCM/HKDF vector with the Android implementation. */
public final class RelayCryptoVector {
    public static void main(String[] arguments) throws Exception {
        byte[] secret = new byte[32];
        for (int index = 0; index < secret.length; index++) secret[index] = (byte) index;
        JSONObject envelope = new JSONObject()
                .put("protocolVersion", 1)
                .put("mailboxId", "mb-vector")
                .put("messageId", "msg-vector")
                .put("direction", "node_to_phone")
                .put("expiresAt", 2_000_000_000L)
                .put("nonce", "AAECAwQFBgcICQoL")
                .put("ciphertext", "cG2vb6jGpWtwcWmIHUDZsfTOsGtpwZcVxixlxOJx-ih2h-qXFRiwbF5rBm3cZ1OkUr42sgpUCShBuHEt5BE-oEY");
        JSONObject value = RelayCrypto.decrypt(secret, envelope, "node_to_phone");
        if (!"cross-language".equals(value.getString("taskId"))) {
            throw new AssertionError("Python envelope was not decrypted by Android implementation");
        }
        JSONObject roundTrip = RelayCrypto.decrypt(secret,
                RelayCrypto.encrypt(secret, "mb-vector", "phone_to_node",
                        new JSONObject().put("kind", "captcha_result")),
                "phone_to_node");
        if (!"captcha_result".equals(roundTrip.getString("kind"))) {
            throw new AssertionError("Android envelope round trip failed");
        }
        System.out.println("RelayCrypto cross-language vector OK");
    }
}
