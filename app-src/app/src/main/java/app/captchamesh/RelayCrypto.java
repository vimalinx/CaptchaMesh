package app.captchamesh;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

final class RelayCrypto {
    private static final byte[] SALT = "CaptchaMesh relay v1\0".getBytes(StandardCharsets.UTF_8);

    static byte[] decode(String value) {
        return java.util.Base64.getUrlDecoder().decode(value);
    }

    static String encode(byte[] value) {
        return java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    private static byte[] hmac(byte[] key, byte[] value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(value);
    }

    private static byte[] key(byte[] secret, String direction) throws Exception {
        if (secret.length != 32) throw new IllegalArgumentException("invalid pair secret");
        byte[] prk = hmac(SALT, secret);
        byte[] label = ("message-key\0" + direction).getBytes(StandardCharsets.US_ASCII);
        byte[] input = new byte[label.length + 1];
        System.arraycopy(label, 0, input, 0, label.length);
        input[input.length - 1] = 1;
        return hmac(prk, input);
    }

    private static byte[] aad(String mailbox, String message, String direction, long expires) {
        return ("captchamesh-relay-v1\n" + mailbox + "\n" + message + "\n"
                + direction + "\n" + expires).getBytes(StandardCharsets.UTF_8);
    }

    static JSONObject decrypt(byte[] secret, JSONObject envelope, String expectedDirection)
            throws Exception {
        if (envelope.getInt("protocolVersion") != 1) throw new IllegalArgumentException("protocol");
        String direction = envelope.getString("direction");
        if (!MessageDigest.isEqual(direction.getBytes(StandardCharsets.UTF_8),
                expectedDirection.getBytes(StandardCharsets.UTF_8))) {
            throw new IllegalArgumentException("direction");
        }
        long expires = envelope.getLong("expiresAt");
        if (expires <= System.currentTimeMillis() / 1000L) throw new IllegalArgumentException("expired");
        String mailbox = envelope.getString("mailboxId");
        String message = envelope.getString("messageId");
        byte[] nonce = decode(envelope.getString("nonce"));
        if (nonce.length != 12) throw new IllegalArgumentException("nonce");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key(secret, direction), "AES"),
                new GCMParameterSpec(128, nonce));
        cipher.updateAAD(aad(mailbox, message, direction, expires));
        return new JSONObject(new String(cipher.doFinal(decode(envelope.getString("ciphertext"))),
                StandardCharsets.UTF_8));
    }

    static JSONObject encrypt(byte[] secret, String mailbox, String direction, JSONObject payload)
            throws Exception {
        String message = "msg-" + java.util.UUID.randomUUID().toString().replace("-", "");
        long expires = System.currentTimeMillis() / 1000L + 600;
        byte[] nonce = new byte[12];
        new java.security.SecureRandom().nextBytes(nonce);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key(secret, direction), "AES"),
                new GCMParameterSpec(128, nonce));
        cipher.updateAAD(aad(mailbox, message, direction, expires));
        return new JSONObject()
                .put("protocolVersion", 1)
                .put("mailboxId", mailbox)
                .put("messageId", message)
                .put("direction", direction)
                .put("expiresAt", expires)
                .put("nonce", encode(nonce))
                .put("ciphertext", encode(cipher.doFinal(
                        payload.toString().getBytes(StandardCharsets.UTF_8))));
    }
}
