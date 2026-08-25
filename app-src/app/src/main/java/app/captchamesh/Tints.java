package app.captchamesh;

import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;

final class Tints {
    static final int BACKGROUND = Color.parseColor("#0F172A");
    static final int SURFACE = Color.parseColor("#111827");
    static final int SURFACE_RAISED = Color.parseColor("#1B2336");
    static final int SURFACE_MUTED = Color.parseColor("#272F42");
    static final int BORDER = Color.parseColor("#334155");
    static final int BORDER_STRONG = Color.parseColor("#64748B");

    static final int TEXT = Color.parseColor("#F8FAFC");
    static final int TEXT_SECONDARY = Color.parseColor("#CBD5E1");
    static final int TEXT_MUTED = Color.parseColor("#94A3B8");

    static final int ACCENT = Color.parseColor("#22C55E");
    static final int ACCENT_PRESSED = Color.parseColor("#16A34A");
    static final int ACCENT_SOFT = Color.parseColor("#143623");
    static final int ON_ACCENT = Color.parseColor("#0F172A");

    static final int INFO = Color.parseColor("#38BDF8");
    static final int INFO_SOFT = Color.parseColor("#12354A");
    static final int WARNING = Color.parseColor("#FBBF24");
    static final int WARNING_SOFT = Color.parseColor("#3F3214");
    static final int DANGER = Color.parseColor("#FB7185");
    static final int DANGER_SOFT = Color.parseColor("#451B26");

    static final int DISABLED_BACKGROUND = Color.parseColor("#263247");
    static final int DISABLED_TEXT = Color.parseColor("#8090A8");

    private Tints() {}

    static GradientDrawable rounded(int color, float radiusPx) {
        return rounded(color, radiusPx, Color.TRANSPARENT, 0);
    }

    static GradientDrawable rounded(int color, float radiusPx, int strokeColor, int strokeWidthPx) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(color);
        background.setCornerRadius(radiusPx);
        if (strokeWidthPx > 0) background.setStroke(strokeWidthPx, strokeColor);
        return background;
    }

    static GradientDrawable hero(float radiusPx, int strokeWidthPx) {
        GradientDrawable background = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{Color.parseColor("#1E293B"), SURFACE});
        background.setCornerRadius(radiusPx);
        background.setStroke(strokeWidthPx, BORDER);
        return background;
    }

    static ColorStateList primaryButtonBackground() {
        return states(ACCENT, ACCENT_PRESSED, DISABLED_BACKGROUND);
    }

    static ColorStateList primaryButtonText() {
        return states(ON_ACCENT, ON_ACCENT, DISABLED_TEXT);
    }

    static ColorStateList secondaryButtonBackground() {
        return states(SURFACE_MUTED, Color.parseColor("#344158"), SURFACE);
    }

    static ColorStateList secondaryButtonText() {
        return states(TEXT, TEXT, DISABLED_TEXT);
    }

    static ColorStateList ripple() {
        return ColorStateList.valueOf(Color.parseColor("#33FFFFFF"));
    }

    static ColorStateList iconTint(int color) {
        return ColorStateList.valueOf(color);
    }

    static ColorStateList navigationItems() {
        return new ColorStateList(
                new int[][]{
                        new int[]{android.R.attr.state_checked},
                        new int[]{}
                },
                new int[]{ACCENT, TEXT_MUTED});
    }

    private static ColorStateList states(int normal, int pressed, int disabled) {
        return new ColorStateList(
                new int[][]{
                        new int[]{-android.R.attr.state_enabled},
                        new int[]{android.R.attr.state_pressed},
                        new int[]{}
                },
                new int[]{disabled, pressed, normal});
    }
}
