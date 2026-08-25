package app.captchamesh;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;

import com.google.android.material.button.MaterialButton;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Native, task-specific manual challenge controls. */
@android.annotation.SuppressLint("ViewConstructor")
final class NativeChallengeView extends LinearLayout {
    interface Callback {
        void onSolved(JSONObject solution);
        void onError(String error);
    }

    private final CaptchaTask task;
    private final Callback callback;

    NativeChallengeView(
            Context context,
            CaptchaTask task,
            Map<String, Bitmap> assets,
            Callback callback) {
        super(context);
        this.task = task;
        this.callback = callback;
        setOrientation(VERTICAL);
        setPadding(dp(16), dp(14), dp(16), dp(16));
        setBackground(Tints.rounded(Tints.SURFACE, dp(14), Tints.BORDER_STRONG, dp(1)));

        String prompt = task.presentation.optString("prompt", "").trim();
        if (!prompt.isEmpty()) {
            TextView promptView = label(prompt, 16, Tints.TEXT, true);
            promptView.setLineSpacing(dp(3), 1.08f);
            addView(promptView, matchWrap());
        }
        Bitmap instruction = assets.get("instructionImage");
        if (instruction != null) {
            ImageView instructionView = imageView(instruction);
            LayoutParams instructionParams = match(dp(96));
            instructionParams.topMargin = dp(10);
            addView(instructionView, instructionParams);
        }

        Bitmap image = assets.get("image");
        if (image == null) {
            callback.onError("challenge_asset_missing");
            return;
        }
        switch (task.presentationKind()) {
            case "image_text": buildImageText(image); break;
            case "coordinates": buildCoordinates(image); break;
            case "grid": buildGrid(image); break;
            case "rotate": buildRotate(image); break;
            default: callback.onError("unsupported_native_challenge");
        }
    }

    private void buildImageText(Bitmap bitmap) {
        ImageView image = imageView(bitmap);
        LayoutParams imageParams = match(dp(230));
        imageParams.topMargin = dp(12);
        addView(image, imageParams);

        int minimum = task.presentation.optInt("minLength", 1);
        int maximum = task.presentation.optInt("maxLength", 1_024);
        int numericMode = task.presentation.optInt("numericMode", 0);
        List<String> rules = new ArrayList<>();
        if (minimum > 1 || maximum < 1_024) rules.add(minimum + "–" + maximum + " 个字符");
        if (task.presentation.optBoolean("phrase", false)) rules.add("至少两个单词");
        if (task.presentation.optBoolean("caseSensitive", false)) rules.add("区分大小写");
        if (task.presentation.optBoolean("math", false)) rules.add("请计算后填写结果");
        if (numericMode == 1) rules.add("仅数字");
        if (numericMode == 2) rules.add("仅字母");
        if (numericMode == 3) rules.add("全数字或全字母");
        if (numericMode == 4) rules.add("必须同时包含数字和字母");
        if (!rules.isEmpty()) {
            TextView ruleView = label(String.join(" · ", rules), 13, Tints.TEXT_SECONDARY, false);
            addTop(ruleView, 10);
        }

        EditText answer = new EditText(getContext());
        answer.setTextColor(Tints.TEXT);
        answer.setHintTextColor(Tints.TEXT_MUTED);
        answer.setHint("输入答案");
        answer.setTextSize(18);
        answer.setSingleLine(true);
        answer.setImeOptions(EditorInfo.IME_ACTION_DONE);
        answer.setPadding(dp(14), 0, dp(14), 0);
        answer.setMinHeight(dp(56));
        answer.setBackground(Tints.rounded(Tints.SURFACE_MUTED, dp(10), Tints.BORDER_STRONG, dp(1)));
        if (numericMode == 1) {
            answer.setInputType(InputType.TYPE_CLASS_NUMBER);
        }
        LayoutParams answerParams = match(dp(56));
        answerParams.topMargin = dp(12);
        addView(answer, answerParams);

        MaterialButton submit = primaryButton("提交答案");
        submit.setEnabled(false);
        answer.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence value, int start, int before, int count) {
                int length = value.toString().length();
                submit.setEnabled(length >= minimum && length <= maximum);
            }
            @Override public void afterTextChanged(Editable value) {}
        });
        submit.setOnClickListener(view -> {
            try {
                callback.onSolved(new JSONObject().put("text", answer.getText().toString()));
            } catch (Exception exception) {
                callback.onError("answer_encode_failed");
            }
        });
        answer.setOnEditorActionListener((view, action, event) -> {
            if (action == EditorInfo.IME_ACTION_DONE && submit.isEnabled()) {
                submit.performClick();
                return true;
            }
            return false;
        });
        addAction(submit);
        answer.requestFocus();
    }

    private void buildCoordinates(Bitmap bitmap) {
        int minimum = task.presentation.optInt("minClicks", 1);
        int maximum = task.presentation.optInt("maxClicks", 100);
        CoordinateImage image = new CoordinateImage(getContext(), bitmap,
                task.presentation.optBoolean("multiple", true), maximum);
        addImageCanvas(image);
        TextView count = label("请选择 " + minimum + "–" + maximum + " 个位置",
                13, Tints.TEXT_SECONDARY, false);
        addTop(count, 10);
        LinearLayout tools = actionRow();
        MaterialButton undo = secondaryButton("撤销");
        undo.setOnClickListener(view -> image.undo());
        MaterialButton clear = secondaryButton("清空");
        clear.setOnClickListener(view -> image.clear());
        tools.addView(undo, weightedButton());
        tools.addView(clear, weightedButtonWithStart());
        addTop(tools, 10);

        MaterialButton submit = primaryButton("提交坐标");
        submit.setEnabled(false);
        image.onChanged = () -> {
            count.setText(getContext().getString(
                    R.string.selected_positions, image.points.size()));
            submit.setEnabled(image.points.size() >= minimum && image.points.size() <= maximum);
        };
        submit.setOnClickListener(view -> {
            try {
                JSONArray points = new JSONArray();
                for (Point point : image.points) {
                    points.put(new JSONObject().put("x", point.x).put("y", point.y));
                }
                callback.onSolved(new JSONObject().put("coordinates", points));
            } catch (Exception exception) {
                callback.onError("coordinates_encode_failed");
            }
        });
        addAction(submit);
    }

    private void buildGrid(Bitmap bitmap) {
        int rows = task.presentation.optInt("rows", 0);
        int columns = task.presentation.optInt("columns", 0);
        int minimum = task.presentation.optInt("minClicks", 1);
        int maximum = task.presentation.optInt("maxClicks", rows * columns);
        GridImage image = new GridImage(getContext(), bitmap, rows, columns, maximum);
        addImageCanvas(image);
        TextView count = label("请选择 " + minimum + "–" + maximum + " 个格子",
                13, Tints.TEXT_SECONDARY, false);
        addTop(count, 10);

        MaterialButton clear = secondaryButton("清空选择");
        clear.setOnClickListener(view -> image.clear());
        addTop(clear, 10);

        MaterialButton submit = primaryButton("提交选择");
        submit.setEnabled(false);
        image.onChanged = () -> {
            count.setText(getContext().getString(
                    R.string.selected_cells, image.selected.size()));
            submit.setEnabled(image.selected.size() >= minimum && image.selected.size() <= maximum);
        };
        submit.setOnClickListener(view -> {
            try {
                JSONArray click = new JSONArray();
                for (Integer index : image.selected) click.put(index);
                callback.onSolved(new JSONObject().put("click", click));
            } catch (Exception exception) {
                callback.onError("grid_encode_failed");
            }
        });
        addAction(submit);
    }

    private void buildRotate(Bitmap bitmap) {
        ImageView image = imageView(bitmap);
        LayoutParams imageParams = match(dp(260));
        imageParams.topMargin = dp(12);
        addView(image, imageParams);

        double requestedStep = task.presentation.optDouble("angleStep", 1);
        double step = requestedStep > 0 ? requestedStep : 1;
        int steps = Math.max(1, (int) Math.round(360d / step));
        TextView angle = label("0°", 20, Tints.ACCENT, true);
        angle.setGravity(Gravity.CENTER);
        addTop(angle, 10);

        SeekBar seek = new SeekBar(getContext());
        seek.setMax(steps);
        seek.setMin(0);
        seek.setContentDescription("旋转角度");
        addTop(seek, 8);

        Runnable update = () -> {
            double value = Math.min(360d, seek.getProgress() * step);
            image.setRotation((float) value);
            angle.setText(formatAngle(value));
        };
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                update.run();
            }
            @Override public void onStartTrackingTouch(SeekBar bar) {}
            @Override public void onStopTrackingTouch(SeekBar bar) {}
        });

        LinearLayout alternatives = actionRow();
        MaterialButton counterClockwise = secondaryButton("向左");
        counterClockwise.setContentDescription("向左旋转一步");
        counterClockwise.setOnClickListener(view -> seek.setProgress(Math.max(0, seek.getProgress() - 1), true));
        MaterialButton clockwise = secondaryButton("向右");
        clockwise.setContentDescription("向右旋转一步");
        clockwise.setOnClickListener(view -> seek.setProgress(Math.min(steps, seek.getProgress() + 1), true));
        alternatives.addView(counterClockwise, weightedButton());
        alternatives.addView(clockwise, weightedButtonWithStart());
        addTop(alternatives, 8);

        MaterialButton submit = primaryButton("提交角度");
        submit.setOnClickListener(view -> {
            try {
                double value = Math.min(360d, seek.getProgress() * step);
                callback.onSolved(new JSONObject().put("rotate", value));
            } catch (Exception exception) {
                callback.onError("angle_encode_failed");
            }
        });
        addAction(submit);
    }

    private String formatAngle(double value) {
        if (value == Math.rint(value)) return String.format(Locale.ROOT, "%.0f°", value);
        return String.format(Locale.ROOT, "%.1f°", value);
    }

    private ImageView imageView(Bitmap bitmap) {
        ImageView image = new ImageView(getContext());
        image.setImageBitmap(bitmap);
        image.setScaleType(ImageView.ScaleType.FIT_CENTER);
        image.setAdjustViewBounds(true);
        image.setBackground(Tints.rounded(Color.WHITE, dp(10), Tints.BORDER_STRONG, dp(1)));
        image.setContentDescription("验证码图片");
        return image;
    }

    private void addImageCanvas(View image) {
        LayoutParams params = match(dp(320));
        params.topMargin = dp(12);
        addView(image, params);
    }

    private void addAction(View view) {
        LayoutParams params = match(dp(52));
        params.topMargin = dp(12);
        addView(view, params);
    }

    private void addTop(View view, int top) {
        LayoutParams params = new LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(top);
        addView(view, params);
    }

    private LinearLayout actionRow() {
        LinearLayout row = new LinearLayout(getContext());
        row.setOrientation(HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        return row;
    }

    private MaterialButton primaryButton(String text) {
        MaterialButton button = new MaterialButton(getContext());
        button.setText(text);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setCornerRadius(dp(10));
        button.setBackgroundTintList(Tints.primaryButtonBackground());
        button.setTextColor(Tints.primaryButtonText());
        return button;
    }

    private MaterialButton secondaryButton(String text) {
        MaterialButton button = new MaterialButton(getContext());
        button.setText(text);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setCornerRadius(dp(10));
        button.setBackgroundTintList(Tints.secondaryButtonBackground());
        button.setTextColor(Tints.secondaryButtonText());
        return button;
    }

    private TextView label(String text, int size, int color, boolean bold) {
        TextView view = new TextView(getContext());
        view.setText(text);
        view.setTextSize(size);
        view.setTextColor(color);
        if (bold) view.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        return view;
    }

    private LayoutParams match(int height) {
        return new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, height);
    }

    private LayoutParams matchWrap() {
        return new LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LayoutParams weightedButton() {
        return new LayoutParams(0, dp(48), 1);
    }

    private LayoutParams weightedButtonWithStart() {
        LayoutParams params = weightedButton();
        params.leftMargin = dp(8);
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static final class Point {
        final int x;
        final int y;
        Point(int x, int y) { this.x = x; this.y = y; }
    }

    private abstract static class ChallengeImage extends View {
        final Bitmap bitmap;
        final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        final RectF destination = new RectF();
        Runnable onChanged = () -> {};

        ChallengeImage(Context context, Bitmap bitmap) {
            super(context);
            this.bitmap = bitmap;
            setBackgroundColor(Color.WHITE);
            setContentDescription("可交互验证码图片");
        }

        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float scale = Math.min(getWidth() / (float) bitmap.getWidth(),
                    getHeight() / (float) bitmap.getHeight());
            float width = bitmap.getWidth() * scale;
            float height = bitmap.getHeight() * scale;
            float left = (getWidth() - width) / 2f;
            float top = (getHeight() - height) / 2f;
            destination.set(left, top, left + width, top + height);
            canvas.drawBitmap(bitmap, null, destination, paint);
        }

        int imageX(float viewX) {
            return Math.max(0, Math.min(bitmap.getWidth() - 1,
                    Math.round((viewX - destination.left) * bitmap.getWidth() / destination.width())));
        }

        int imageY(float viewY) {
            return Math.max(0, Math.min(bitmap.getHeight() - 1,
                    Math.round((viewY - destination.top) * bitmap.getHeight() / destination.height())));
        }
    }

    private static final class CoordinateImage extends ChallengeImage {
        final List<Point> points = new ArrayList<>();
        final boolean multiple;
        final int maximum;

        CoordinateImage(Context context, Bitmap bitmap, boolean multiple, int maximum) {
            super(context, bitmap);
            this.multiple = multiple;
            this.maximum = maximum;
        }

        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float density = getResources().getDisplayMetrics().density;
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Tints.ACCENT);
            for (int index = 0; index < points.size(); index++) {
                Point point = points.get(index);
                float x = destination.left + point.x * destination.width() / bitmap.getWidth();
                float y = destination.top + point.y * destination.height() / bitmap.getHeight();
                canvas.drawCircle(x, y, 14 * density, paint);
                paint.setColor(Tints.ON_ACCENT);
                paint.setTextAlign(Paint.Align.CENTER);
                paint.setTextSize(13 * density);
                canvas.drawText(String.valueOf(index + 1), x, y + 4.5f * density, paint);
                paint.setColor(Tints.ACCENT);
            }
        }

        @Override public boolean onTouchEvent(MotionEvent event) {
            if (event.getAction() != MotionEvent.ACTION_UP) return true;
            if (!destination.contains(event.getX(), event.getY())) return true;
            if (!multiple) points.clear();
            if (points.size() >= maximum) return true;
            points.add(new Point(imageX(event.getX()), imageY(event.getY())));
            invalidate();
            onChanged.run();
            performClick();
            return true;
        }

        @Override public boolean performClick() {
            super.performClick();
            return true;
        }

        void undo() {
            if (!points.isEmpty()) points.remove(points.size() - 1);
            invalidate();
            onChanged.run();
        }

        void clear() {
            points.clear();
            invalidate();
            onChanged.run();
        }
    }

    private static final class GridImage extends ChallengeImage {
        final int rows;
        final int columns;
        final int maximum;
        final Set<Integer> selected = new LinkedHashSet<>();

        GridImage(Context context, Bitmap bitmap, int rows, int columns, int maximum) {
            super(context, bitmap);
            this.rows = rows;
            this.columns = columns;
            this.maximum = maximum;
        }

        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(105, 34, 197, 94));
            for (Integer index : selected) {
                int zero = index - 1;
                int row = zero / columns;
                int column = zero % columns;
                float left = destination.left + column * destination.width() / columns;
                float top = destination.top + row * destination.height() / rows;
                float right = destination.left + (column + 1) * destination.width() / columns;
                float bottom = destination.top + (row + 1) * destination.height() / rows;
                canvas.drawRect(left, top, right, bottom, paint);
            }
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(getResources().getDisplayMetrics().density);
            paint.setColor(Color.WHITE);
            for (int row = 1; row < rows; row++) {
                float y = destination.top + row * destination.height() / rows;
                canvas.drawLine(destination.left, y, destination.right, y, paint);
            }
            for (int column = 1; column < columns; column++) {
                float x = destination.left + column * destination.width() / columns;
                canvas.drawLine(x, destination.top, x, destination.bottom, paint);
            }
        }

        @Override public boolean onTouchEvent(MotionEvent event) {
            if (event.getAction() != MotionEvent.ACTION_UP) return true;
            if (!destination.contains(event.getX(), event.getY())) return true;
            int column = Math.min(columns - 1,
                    (int) ((event.getX() - destination.left) * columns / destination.width()));
            int row = Math.min(rows - 1,
                    (int) ((event.getY() - destination.top) * rows / destination.height()));
            int index = row * columns + column + 1;
            if (selected.contains(index)) {
                selected.remove(index);
            } else if (selected.size() < maximum) {
                selected.add(index);
            }
            invalidate();
            onChanged.run();
            performClick();
            return true;
        }

        @Override public boolean performClick() {
            super.performClick();
            return true;
        }

        void clear() {
            selected.clear();
            invalidate();
            onChanged.run();
        }
    }
}
