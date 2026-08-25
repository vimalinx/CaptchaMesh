package app.captchamesh;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class CaptchaTask {
    final String id;
    final String type;
    final String websiteUrl;
    final String websiteKey;
    final String mode;
    final int timeoutSeconds;
    final boolean invisible;
    final JSONObject task;
    final JSONObject context;
    final JSONObject presentation;

    CaptchaTask(JSONObject envelope) throws Exception {
        id = envelope.getString("taskId");
        task = envelope.getJSONObject("task");
        context = envelope.optJSONObject("context") == null
                ? new JSONObject() : envelope.getJSONObject("context");
        type = task.getString("type");
        websiteUrl = task.getString("websiteURL");
        websiteKey = task.optString("websiteKey", "");
        mode = task.optString("mode", "auto");
        timeoutSeconds = task.optInt("timeoutSeconds", 120);
        invisible = task.optBoolean("isInvisible", mode.equals("auto"));
        presentation = task.optJSONObject("presentation") == null
                ? new JSONObject() : task.optJSONObject("presentation");
    }

    String host() {
        try {
            return new java.net.URL(websiteUrl).getHost();
        } catch (Exception ignored) {
            return websiteUrl;
        }
    }

    boolean interactive() {
        return mode.equals("interactive");
    }

    boolean structured() {
        String kind = presentation.optString("kind", "");
        return kind.equals("image_text") || kind.equals("coordinates")
                || kind.equals("grid") || kind.equals("rotate");
    }

    String presentationKind() {
        return presentation.optString("kind", type);
    }

    String assetId(String name) {
        JSONObject descriptor = presentation.optJSONObject(name);
        return descriptor == null ? "" : descriptor.optString("assetId", "");
    }

    List<String> assetNames() {
        List<String> names = new ArrayList<>();
        if (!assetId("image").isEmpty()) names.add("image");
        if (!assetId("instructionImage").isEmpty()) names.add("instructionImage");
        return names;
    }
}
