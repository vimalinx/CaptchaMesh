package app.captchamesh;

import android.app.NotificationManager;
import android.content.Context;

final class NotificationPreferences {
    private static final String PREFERENCES = "cm";
    private static final String TASK_ALERTS = "notify_task_alerts";
    private static final int WORKFLOW_CHALLENGE_ID = 4102;
    private static final int AGENT_CHALLENGE_ID = 4202;

    private NotificationPreferences() { }

    static boolean taskAlertsEnabled(Context context) {
        return context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .getBoolean(TASK_ALERTS, true);
    }

    static void setTaskAlertsEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit().putBoolean(TASK_ALERTS, enabled).apply();
        if (!enabled) {
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            manager.cancel(WORKFLOW_CHALLENGE_ID);
            manager.cancel(AGENT_CHALLENGE_ID);
        }
    }
}
