package app.captchamesh;

import android.app.Application;

public final class CaptchaMeshApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            DiagnosticLog.error(this, "APP", "UNCAUGHT", throwable);
            if (previous != null) previous.uncaughtException(thread, throwable);
        });
        DiagnosticLog.event(this, "APP", "STARTED");
    }
}
