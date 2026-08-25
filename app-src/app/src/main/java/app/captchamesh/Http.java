package app.captchamesh;

import java.io.IOException;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

final class Http {
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");

    static final class Result {
        final int code;
        final String body;

        Result(int code, String body) {
            this.code = code;
            this.body = body;
        }
    }

    static Result post(OkHttpClient client, String url, String json, String authorization)
            throws IOException {
        Request.Builder builder = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .post(RequestBody.create(json, JSON));
        if (authorization != null && !authorization.isEmpty()) {
            builder.header("Authorization", authorization);
        }
        try (Response response = client.newCall(builder.build()).execute()) {
            String body = response.body() == null ? "" : response.body().string();
            if (!response.isSuccessful() && response.code() != 204) {
                throw new IOException("broker HTTP " + response.code());
            }
            return new Result(response.code(), body);
        }
    }

    static Result get(OkHttpClient client, String url, String authorization) throws IOException {
        Request.Builder builder = new Request.Builder().url(url).header("Accept", "application/json");
        if (authorization != null && !authorization.isEmpty()) {
            builder.header("Authorization", authorization);
        }
        try (Response response = client.newCall(builder.build()).execute()) {
            String body = response.body() == null ? "" : response.body().string();
            if (!response.isSuccessful()) {
                throw new IOException("broker HTTP " + response.code());
            }
            return new Result(response.code(), body);
        }
    }

    static byte[] getBytes(OkHttpClient client, String url, String authorization)
            throws IOException {
        Request.Builder builder = new Request.Builder()
                .url(url)
                .header("Accept", "image/*");
        if (authorization != null && !authorization.isEmpty()) {
            builder.header("Authorization", authorization);
        }
        try (Response response = client.newCall(builder.build()).execute()) {
            if (!response.isSuccessful() || response.body() == null) {
                throw new IOException("broker asset HTTP " + response.code());
            }
            return response.body().bytes();
        }
    }
}
