package app.captchamesh;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.ProxyConfig;
import androidx.webkit.ProxyController;
import androidx.webkit.WebViewFeature;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

final class Solver {
    interface Ui {
        void showChallenge(View challenge, CaptchaTask task);
        void clearChallenge(View challenge);
    }

    static final class Solution {
        final JSONObject value;
        Solution(JSONObject value) {
            this.value = value;
        }
    }

    static final class Bridge {
        final AtomicReference<JSONObject> solution;
        final AtomicReference<String> error;
        final CountDownLatch done;

        Bridge(AtomicReference<JSONObject> solution, AtomicReference<String> error, CountDownLatch done) {
            this.solution = solution;
            this.error = error;
            this.done = done;
        }

        @JavascriptInterface
        public void onToken(String value) {
            if (value != null && value.length() >= 20) {
                try {
                    accept(new JSONObject().put("token", value));
                } catch (Exception ignored) { }
            }
        }

        @JavascriptInterface
        public void onSolution(String value) {
            try {
                accept(new JSONObject(value));
            } catch (Exception exception) {
                onError("solution_json_invalid");
            }
        }

        private void accept(JSONObject value) {
            if (solution.compareAndSet(null, value)) done.countDown();
        }

        @JavascriptInterface
        public void onError(String value) {
            if (solution.get() == null && error.compareAndSet(null, value)) {
                done.countDown();
            }
        }
    }

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Ui ui;
    private final AtomicReference<WebView> current = new AtomicReference<>();
    private volatile boolean shutdown;
    private volatile boolean proxyConfigured;

    Solver(Context context, Ui ui) {
        this.context = context;
        this.ui = ui;
    }

    @SuppressLint({"SetJavaScriptEnabled"})
    Solution solve(CaptchaTask task, Map<String, Bitmap> assets) throws Exception {
        if (shutdown) throw new IllegalStateException("solver stopped");
        if (task.structured()) return solveStructured(task, assets);
        String proxy = task.context.optString("proxy", "");
        if (!proxy.isEmpty()) warmUpWebView();
        configureProxy(proxy);
        clearCookies();

        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<JSONObject> solution = new AtomicReference<>();
        AtomicReference<String> error = new AtomicReference<>();
        Bridge bridge = new Bridge(solution, error, done);

        main.post(() -> {
            try {
                clearBrowserState(false);
                installCookies(task);
                WebView webView = new WebView(context);
                current.set(webView);
                WebSettings settings = webView.getSettings();
                settings.setJavaScriptEnabled(true);
                settings.setDomStorageEnabled(true);
                settings.setDatabaseEnabled(true);
                settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
                settings.setAllowFileAccess(false);
                settings.setAllowContentAccess(false);
                settings.setJavaScriptCanOpenWindowsAutomatically(false);
                settings.setSupportMultipleWindows(false);
                String requestedAgent = task.context.optString("userAgent", "");
                if (!requestedAgent.isEmpty()) settings.setUserAgentString(requestedAgent);
                webView.addJavascriptInterface(bridge, "CaptchaMeshBridge");

                AtomicBoolean storageInstalled = new AtomicBoolean(false);
                AtomicBoolean adapterInjected = new AtomicBoolean(false);
                webView.setWebViewClient(new WebViewClient() {
                    @Override
                    public void onPageFinished(WebView view, String url) {
                        if (!storageInstalled.getAndSet(true) && task.context.has("localStorage")) {
                            view.evaluateJavascript(localStorageScript(task.context), ignored -> view.reload());
                            return;
                        }
                        if (adapterInjected.compareAndSet(false, true)) {
                            if (task.type.equals("datadome")) {
                                startDataDomeCookiePoll(task, solution, error, done);
                            } else {
                                view.evaluateJavascript(adapterScript(task), null);
                            }
                        }
                    }

                    @Override
                    public void onReceivedError(
                            WebView view, WebResourceRequest request, WebResourceError webError) {
                        if (request.isForMainFrame() && solution.get() == null) {
                            error.compareAndSet(null, "page_load_" + webError.getErrorCode());
                            done.countDown();
                        }
                    }

                    @Override
                    public boolean onRenderProcessGone(
                            WebView view, RenderProcessGoneDetail detail) {
                        current.compareAndSet(view, null);
                        ui.clearChallenge(view);
                        view.destroy();
                        if (solution.get() == null) {
                            error.compareAndSet(null, detail.didCrash()
                                    ? "webview_renderer_crashed" : "webview_renderer_killed");
                            done.countDown();
                        }
                        return true;
                    }
                });
                if (task.interactive()) ui.showChallenge(webView, task);
                if (task.type.equals("webview")) {
                    webView.loadUrl(task.websiteUrl, requestHeaders(task.context));
                } else if (task.type.equals("datadome")) {
                    webView.loadUrl(task.task.optString("captchaUrl"), requestHeaders(task.context));
                } else {
                    webView.loadDataWithBaseURL(
                            task.websiteUrl, challengeShell(), "text/html", "UTF-8", null);
                }
            } catch (Exception exception) {
                error.set("setup_" + exception.getClass().getSimpleName());
                done.countDown();
            }
        });

        boolean completed = done.await(task.timeoutSeconds + 5L, TimeUnit.SECONDS);
        if (!completed && error.get() == null) error.set("solve_timeout");

        cleanup();

        if (solution.get() != null) {
            return new Solution(solution.get());
        }
        throw new RuntimeException(error.get() == null ? "no_result" : error.get());
    }

    private Solution solveStructured(CaptchaTask task, Map<String, Bitmap> assets) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<JSONObject> solution = new AtomicReference<>();
        AtomicReference<String> error = new AtomicReference<>();
        AtomicReference<View> challenge = new AtomicReference<>();
        main.post(() -> {
            NativeChallengeView view = new NativeChallengeView(
                    context,
                    task,
                    assets,
                    new NativeChallengeView.Callback() {
                        @Override public void onSolved(JSONObject value) {
                            if (solution.compareAndSet(null, value)) done.countDown();
                        }

                        @Override public void onError(String value) {
                            if (error.compareAndSet(null, value)) done.countDown();
                        }
                    });
            challenge.set(view);
            ui.showChallenge(view, task);
        });
        boolean completed = done.await(task.timeoutSeconds + 5L, TimeUnit.SECONDS);
        if (!completed && error.get() == null) error.set("solve_timeout");
        main.post(() -> {
            View view = challenge.getAndSet(null);
            if (view != null) ui.clearChallenge(view);
        });
        if (solution.get() != null) return new Solution(solution.get());
        throw new RuntimeException(error.get() == null ? "no_result" : error.get());
    }

    private String challengeShell() {
        return "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>html,body{margin:0;min-height:100%;background:#fff;font-family:sans-serif}"
                + "body{display:flex;align-items:flex-start;justify-content:center;padding:18px;box-sizing:border-box}"
                + "#captchamesh-challenge{width:100%;display:flex;justify-content:center}</style></head>"
                + "<body><div id='captchamesh-challenge'></div></body></html>";
    }

    private Map<String, String> requestHeaders(JSONObject context) {
        Map<String, String> headers = new HashMap<>();
        JSONObject source = context.optJSONObject("headers");
        if (source == null) return headers;
        Iterator<String> keys = source.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            headers.put(key, source.optString(key, ""));
        }
        return headers;
    }

    private void installCookies(CaptchaTask task) {
        JSONArray cookies = task.context.optJSONArray("cookies");
        if (cookies == null) return;
        CookieManager manager = CookieManager.getInstance();
        manager.setAcceptCookie(true);
        for (int index = 0; index < cookies.length(); index++) {
            JSONObject cookie = cookies.optJSONObject(index);
            if (cookie == null) continue;
            StringBuilder value = new StringBuilder();
            value.append(cookie.optString("name")).append("=").append(cookie.optString("value"));
            if (cookie.has("domain")) value.append("; Domain=").append(cookie.optString("domain"));
            value.append("; Path=").append(cookie.optString("path", "/"));
            if (cookie.optBoolean("secure")) value.append("; Secure");
            if (cookie.optBoolean("httpOnly")) value.append("; HttpOnly");
            if (cookie.has("sameSite")) value.append("; SameSite=").append(cookie.optString("sameSite"));
            manager.setCookie(task.websiteUrl, value.toString());
        }
        manager.flush();
    }

    private void clearCookies() throws Exception {
        CountDownLatch cleared = new CountDownLatch(1);
        main.post(() -> CookieManager.getInstance().removeAllCookies(
                ignored -> cleared.countDown()));
        if (!cleared.await(10, TimeUnit.SECONDS)) {
            throw new IllegalStateException("cookie_clear_timeout");
        }
    }

    private String localStorageScript(JSONObject context) {
        JSONObject storage = context.optJSONObject("localStorage");
        StringBuilder script = new StringBuilder("(function(){try{");
        if (storage != null) {
            Iterator<String> keys = storage.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                script.append("localStorage.setItem(")
                        .append(JSONObject.quote(key)).append(",")
                        .append(JSONObject.quote(storage.optString(key, ""))).append(");");
            }
        }
        return script.append("}catch(e){CaptchaMeshBridge.onError('storage_'+e.name);}})();").toString();
    }

    private String adapterScript(CaptchaTask captcha) {
        String type = captcha.type;
        String key = JSONObject.quote(captcha.websiteKey);
        String callback = "function(t){CaptchaMeshBridge.onToken(String(t));}";
        String failure = "function(e){CaptchaMeshBridge.onError('provider_'+String(e||'error'));}";
        if (type.equals("turnstile")) {
            StringBuilder options = new StringBuilder("{sitekey:").append(key)
                    .append(",callback:").append(callback)
                    .append(",'error-callback':").append(failure)
                    .append(",'expired-callback':function(){CaptchaMeshBridge.onError('expired');}")
                    .append(",size:").append(JSONObject.quote(captcha.invisible ? "invisible" : "normal"));
            for (String field : new String[]{"action", "cData", "chlPageData"}) {
                if (captcha.task.has(field)) {
                    options.append(",").append(field).append(":")
                            .append(JSONObject.quote(captcha.task.optString(field)));
                }
            }
            options.append("}");
            return loadScript(
                    "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
                    "window.turnstile",
                    "var d=document.createElement('div');document.body.appendChild(d);"
                            + "turnstile.render(d," + options + ");");
        }
        if (type.equals("hcaptcha")) {
            StringBuilder options = new StringBuilder("{sitekey:").append(key)
                    .append(",callback:").append(callback)
                    .append(",'error-callback':").append(failure);
            if (captcha.invisible) options.append(",size:'invisible'");
            if (captcha.task.has("rqdata")) {
                options.append(",rqdata:").append(JSONObject.quote(captcha.task.optString("rqdata")));
            }
            options.append("}");
            return loadScript(
                    "https://js.hcaptcha.com/1/api.js?render=explicit",
                    "window.hcaptcha",
                    "var d=document.createElement('div');document.body.appendChild(d);"
                            + "var id=hcaptcha.render(d," + options + ");"
                            + (captcha.invisible ? "hcaptcha.execute(id);" : ""));
        }
        if (type.equals("recaptcha_v2")) {
            String options = "{sitekey:" + key + ",callback:" + callback
                    + ",'error-callback':" + failure
                    + (captcha.invisible ? ",size:'invisible'" : "") + "}";
            return loadScript(
                    "https://www.google.com/recaptcha/api.js?render=explicit",
                    "window.grecaptcha && typeof window.grecaptcha.render === 'function'",
                    "var d=document.createElement('div');document.body.appendChild(d);"
                            + "var id=grecaptcha.render(d," + options + ");"
                            + (captcha.invisible ? "grecaptcha.execute(id);" : ""));
        }
        if (type.equals("recaptcha_v3")) {
            String action = JSONObject.quote(captcha.task.optString("action"));
            return loadScript(
                    "https://www.google.com/recaptcha/api.js?render=" + captcha.websiteKey,
                    "window.grecaptcha",
                    "grecaptcha.ready(function(){grecaptcha.execute(" + key + ",{action:" + action
                            + "}).then(" + callback + ").catch(" + failure + ");});");
        }
        if (type.equals("funcaptcha")) {
            String host = captcha.task.optString(
                    "funcaptchaApiJSSubdomain", "client-api.arkoselabs.com")
                    .replaceFirst("^https?://", "").replaceAll("/+$", "");
            StringBuilder options = new StringBuilder("{public_key:").append(key)
                    .append(",target_html:'captchamesh-challenge',callback:").append(callback)
                    .append(",onshown:function(){},onerror:").append(failure);
            Object data = captcha.task.opt("data");
            if (data instanceof JSONObject && ((JSONObject) data).has("blob")) {
                options.append(",data:{blob:")
                        .append(JSONObject.quote(((JSONObject) data).optString("blob")))
                        .append("}");
            } else if (data instanceof String && !((String) data).isEmpty()) {
                options.append(",data:{blob:").append(JSONObject.quote((String) data)).append("}");
            }
            options.append("}");
            return loadScript(
                    "https://" + host + "/fc/api/?onload=CaptchaMeshArkoseLoaded",
                    "window.FunCaptcha",
                    "new FunCaptcha(" + options + ");");
        }
        if (type.equals("geetest_v3")) {
            String gt = JSONObject.quote(captcha.task.optString("gt"));
            String challenge = JSONObject.quote(captcha.task.optString("challenge"));
            String apiServer = captcha.task.optString("geetestApiServerSubdomain", "");
            String apiOption = apiServer.isEmpty()
                    ? "" : ",api_server:" + JSONObject.quote(apiServer);
            String execute = "initGeetest({gt:" + gt + ",challenge:" + challenge
                    + ",offline:false,new_captcha:true,product:'popup'" + apiOption
                    + "},function(c){c.appendTo('#captchamesh-challenge');"
                    + "c.onSuccess(function(){var v=c.getValidate()||{};"
                    + "CaptchaMeshBridge.onSolution(JSON.stringify({challenge:v.geetest_challenge||"
                    + challenge + ",validate:v.geetest_validate||'',seccode:v.geetest_seccode||''}));});"
                    + "c.onError(" + failure + ");});";
            return loadScript(
                    "https://static.geetest.com/static/tools/gt.js",
                    "window.initGeetest",
                    execute);
        }
        if (type.equals("geetest_v4")) {
            String captchaId = JSONObject.quote(captcha.task.optString("captchaId"));
            String riskType = captcha.task.optString("riskType", "");
            String riskOption = riskType.isEmpty()
                    ? "" : ",riskType:" + JSONObject.quote(riskType);
            String execute = "initGeetest4({captchaId:" + captchaId + ",product:'popup'"
                    + riskOption + "},function(c){c.appendTo('#captchamesh-challenge');"
                    + "c.onSuccess(function(){var v=c.getValidate()||{};"
                    + "CaptchaMeshBridge.onSolution(JSON.stringify({captcha_id:" + captchaId
                    + ",lot_number:v.lot_number||'',pass_token:v.pass_token||'',gen_time:v.gen_time||'',"
                    + "captcha_output:v.captcha_output||''}));});c.onError(" + failure + ");});";
            return loadScript(
                    "https://static.geetest.com/v4/gt4.js",
                    "window.initGeetest4",
                    execute);
        }
        if (type.equals("amazon_waf")) {
            String challengeScript = captcha.task.optString("challengeScript", "");
            String captchaScript = captcha.task.optString("captchaScript", "");
            String jsapiScript = captcha.task.optString("jsapiScript", "");
            if (!jsapiScript.isEmpty()) {
                String execute = "AwsWafCaptcha.renderCaptcha(document.getElementById('captchamesh-challenge'),"
                    + "{apiKey:" + key + ",onSuccess:function(v){CaptchaMeshBridge.onSolution(JSON.stringify("
                    + "{captcha_voucher:String(v||''),existing_token:''}));},onError:"
                    + failure + "});";
                return loadScript(jsapiScript, "window.AwsWafCaptcha", execute);
            }
            String iv = JSONObject.quote(captcha.task.optString("iv"));
            String awsContext = JSONObject.quote(captcha.task.optString("awsContext"));
            return "(function(){window.gokuProps={key:" + key + ",iv:" + iv
                    + ",context:" + awsContext + "};"
                    + "var fail=" + failure + ";"
                    + "var load=function(src,next){var s=document.createElement('script');s.src=src;"
                    + "s.async=false;s.onload=next;s.onerror=function(){CaptchaMeshBridge.onError('script_load');};"
                    + "document.head.appendChild(s);};"
                    + "load(" + JSONObject.quote(challengeScript) + ",function(){load("
                    + JSONObject.quote(captchaScript) + ",function(){var n=0;var i=setInterval(function(){"
                    + "if(window.ChallengeScript&&window.CaptchaScript){clearInterval(i);"
                    + "Promise.resolve(ChallengeScript.getToken()).then(function(existing){"
                    + "CaptchaScript.renderCaptcha(document.getElementById('captchamesh-challenge'),"
                    + "function(voucher){CaptchaMeshBridge.onSolution(JSON.stringify({captcha_voucher:"
                    + "String(voucher||''),existing_token:String(existing||'')}));});}).catch(fail);"
                    + "}else if(++n>300){clearInterval(i);CaptchaMeshBridge.onError('script_timeout');}"
                    + "},200);});});})();";
        }
        if (type.equals("webview")) {
            String selector = JSONObject.quote(captcha.task.optString("responseSelector"));
            String property = JSONObject.quote(captcha.task.optString("responseProperty", "value"));
            return "(function(){var n=0;var i=setInterval(function(){try{var e=document.querySelector("
                    + selector + ");var v=e&&e[" + property + "];if(typeof v==='string'&&v.length>=20){"
                    + "clearInterval(i);CaptchaMeshBridge.onToken(v);}}catch(x){clearInterval(i);"
                    + "CaptchaMeshBridge.onError('selector_'+x.name);}if(++n>900){clearInterval(i);}},200);})();";
        }
        return "CaptchaMeshBridge.onError('unsupported_adapter');";
    }

    private String loadScript(String source, String readyExpression, String execute) {
        return "(function(){var run=function(){var n=0;var i=setInterval(function(){"
                + "if(" + readyExpression + "){clearInterval(i);try{" + execute
                + "}catch(e){CaptchaMeshBridge.onError('adapter_'+e.name+'_'+String(e.message||'').slice(0,80));}}"
                + "else if(++n>300){clearInterval(i);CaptchaMeshBridge.onError('script_timeout');}},200);};"
                + "var s=document.createElement('script');s.src=" + JSONObject.quote(source)
                + ";s.async=true;s.defer=true;s.onload=run;s.onerror=function(){CaptchaMeshBridge.onError('script_load');};"
                + "document.head.appendChild(s);})();";
    }

    private void startDataDomeCookiePoll(
            CaptchaTask task,
            AtomicReference<JSONObject> solution,
            AtomicReference<String> error,
            CountDownLatch done) {
        long deadline = System.currentTimeMillis() + task.timeoutSeconds * 1_000L;
        Runnable poll = new Runnable() {
            @Override public void run() {
                if (solution.get() != null || error.get() != null) return;
                String cookies = CookieManager.getInstance().getCookie(task.websiteUrl);
                if (cookies == null || !cookies.contains("datadome=")) {
                    cookies = CookieManager.getInstance().getCookie(
                            task.task.optString("captchaUrl", task.websiteUrl));
                }
                if (cookies != null) {
                    for (String part : cookies.split(";")) {
                        String candidate = part.trim();
                        if (candidate.startsWith("datadome=") && candidate.length() > 10) {
                            try {
                                if (solution.compareAndSet(
                                        null, new JSONObject().put("cookie", candidate))) {
                                    done.countDown();
                                }
                            } catch (Exception ignored) { }
                            return;
                        }
                    }
                }
                if (System.currentTimeMillis() < deadline) {
                    main.postDelayed(this, 500);
                }
            }
        };
        main.post(poll);
    }

    @SuppressLint("RequiresFeature")
    private void configureProxy(String proxy) throws Exception {
        // The first WebView starts Chromium. Clearing an override before that
        // startup has completed throws on some vendor WebView builds; there is
        // no override to clear before the first task anyway.
        if (proxyConfigured) clearProxyOverride();
        if (proxy.isEmpty()) return;
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
            throw new IllegalStateException("proxy_override_unsupported");
        }
        CountDownLatch changed = new CountDownLatch(1);
        ProxyController controller = ProxyController.getInstance();
        java.net.URI uri = new java.net.URI(proxy);
        if (uri.getUserInfo() != null) {
            throw new IllegalArgumentException("authenticated_proxy_unsupported");
        }
        ProxyConfig config = new ProxyConfig.Builder().addProxyRule(proxy).build();
        controller.setProxyOverride(config, Runnable::run, changed::countDown);
        if (!changed.await(10, TimeUnit.SECONDS)) throw new IllegalStateException("proxy_timeout");
        proxyConfigured = true;
    }

    @SuppressLint("RequiresFeature")
    private void clearProxyOverride() throws Exception {
        if (!proxyConfigured || !WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
            proxyConfigured = false;
            return;
        }
        CountDownLatch cleared = new CountDownLatch(1);
        ProxyController.getInstance().clearProxyOverride(Runnable::run, cleared::countDown);
        if (!cleared.await(10, TimeUnit.SECONDS)) {
            throw new IllegalStateException("proxy_clear_timeout");
        }
        proxyConfigured = false;
    }

    private void warmUpWebView() throws Exception {
        CountDownLatch ready = new CountDownLatch(1);
        main.post(() -> {
            WebView seed = new WebView(context);
            seed.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageFinished(WebView view, String url) {
                    view.destroy();
                    ready.countDown();
                }

                @Override
                public boolean onRenderProcessGone(
                        WebView view, RenderProcessGoneDetail detail) {
                    view.destroy();
                    ready.countDown();
                    return true;
                }
            });
            seed.loadUrl("about:blank");
        });
        if (!ready.await(10, TimeUnit.SECONDS)) {
            throw new IllegalStateException("webview_warmup_timeout");
        }
    }

    private void cleanup() {
        main.post(() -> {
            WebView webView = current.getAndSet(null);
            if (webView != null) {
                ui.clearChallenge(webView);
                webView.stopLoading();
                webView.removeJavascriptInterface("CaptchaMeshBridge");
                webView.loadUrl("about:blank");
                webView.clearHistory();
                webView.destroy();
            }
            clearBrowserState(true);
            CookieManager.getInstance().removeAllCookies(ignored -> { });
        });
        if (WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
            ProxyController.getInstance().clearProxyOverride(
                    Runnable::run, () -> proxyConfigured = false);
        }
    }

    private void clearBrowserState(boolean clearStorage) {
        if (clearStorage) WebStorage.getInstance().deleteAllData();
    }

    void shutdown() {
        shutdown = true;
        cleanup();
    }
}
