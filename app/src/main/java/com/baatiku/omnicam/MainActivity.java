package com.baatiku.omnicam;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

public final class MainActivity extends Activity {
    private static final int PICK_IDENTITY = 30;
    private static final int REQ_PERMS = 31;

    private EditText endpoint;
    private EditText token;
    private TextView identity;
    private TextView status;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int p = (int) (20 * getResources().getDisplayMetrics().density);
        root.setPadding(p, p, p, p);
        scroll.addView(root);

        TextView title = new TextView(this);
        title.setText("OmniCam Live");
        title.setTextSize(26);
        root.addView(title);

        TextView help = new TextView(this);
        help.setText("1. Configure Modal endpoint.\n2. Choose a permitted or synthetic identity image.\n3. Start live camera.\n4. Enable this APK in LSPosed for Zoom.\n5. Force-stop and reopen the original Zoom app.");
        root.addView(help);

        endpoint = new EditText(this);
        endpoint.setHint("https://...modal.run");
        endpoint.setSingleLine(true);
        endpoint.setText(getPreferences(MODE_PRIVATE).getString("endpoint", ""));
        root.addView(endpoint, match());

        token = new EditText(this);
        token.setHint("API token");
        token.setSingleLine(true);
        token.setText(getPreferences(MODE_PRIVATE).getString("token", ""));
        root.addView(token, match());

        identity = new TextView(this);
        identity.setText(identityFile().exists() ? "Identity image ready" : "No identity image selected");
        root.addView(identity);

        Button choose = new Button(this);
        choose.setText("Choose identity image");
        choose.setOnClickListener(v -> chooseIdentity());
        root.addView(choose);

        Button start = new Button(this);
        start.setText("Start live camera");
        start.setOnClickListener(v -> startLive());
        root.addView(start);

        Button stop = new Button(this);
        stop.setText("Stop");
        stop.setOnClickListener(v -> {
            stopService(new Intent(this, StreamingService.class));
            status.setText("Stopped.");
        });
        root.addView(stop);

        status = new TextView(this);
        status.setText("Idle. Virtual-camera injection is active only while the local relay is running.");
        root.addView(status);

        setContentView(scroll);
        ensurePermissions();
    }

    private ViewGroup.LayoutParams match() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private File identityFile() {
        return new File(getFilesDir(), "identity.jpg");
    }

    private void chooseIdentity() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.setType("image/*");
        i.addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(i, PICK_IDENTITY);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PICK_IDENTITY || result != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) return;

        try (InputStream in = getContentResolver().openInputStream(uri);
             FileOutputStream out = new FileOutputStream(identityFile())) {
            if (in == null) throw new IllegalStateException("Unable to open image");
            byte[] buf = new byte[64 * 1024];
            int n;
            long total = 0;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > 4L * 1024L * 1024L) {
                    throw new IllegalArgumentException("Identity image is larger than 4 MB");
                }
                out.write(buf, 0, n);
            }
            identity.setText("Identity image ready");
        } catch (Exception e) {
            identity.setText("Identity import failed: " + e.getMessage());
        }
    }

    private void ensurePermissions() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, REQ_PERMS);
        }
    }

    private void startLive() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            ensurePermissions();
            status.setText("Camera permission is required.");
            return;
        }

        String e = endpoint.getText().toString().trim();
        String t = token.getText().toString().trim();
        if (e.isEmpty() || t.isEmpty()) {
            status.setText("Endpoint and token are required.");
            return;
        }
        if (!identityFile().exists()) {
            status.setText("Choose an identity image first.");
            return;
        }

        getPreferences(MODE_PRIVATE).edit()
                .putString("endpoint", e)
                .putString("token", t)
                .apply();

        Intent i = new Intent(this, StreamingService.class)
                .putExtra("endpoint", e)
                .putExtra("token", t);
        startForegroundService(i);
        status.setText("Starting. Use adb logcat -s OmniCam:* for detailed status.");
    }
}
