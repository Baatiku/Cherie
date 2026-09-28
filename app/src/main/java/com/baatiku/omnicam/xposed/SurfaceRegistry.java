package com.baatiku.omnicam.xposed;

import android.graphics.SurfaceTexture;
import android.util.Log;
import android.view.Surface;

import java.util.IdentityHashMap;
import java.util.Map;

final class SurfaceRegistry {
    private static final String TAG = "OmniCam";

    private final Map<Surface, Entry> entries = new IdentityHashMap<>();

    synchronized Surface replacementFor(Surface original) {
        if (original == null) return null;

        Entry existing = entries.get(original);
        if (existing != null) return existing.dummy;

        SurfaceTexture texture = new SurfaceTexture(false);
        texture.setDefaultBufferSize(640, 480);
        Surface dummy = new Surface(texture);

        LocalH264Decoder decoder = new LocalH264Decoder(original);
        decoder.start();

        entries.put(original, new Entry(texture, dummy, decoder));
        Log.i(TAG, "Mapped Zoom camera Surface to virtual decoder");
        return dummy;
    }

    synchronized Surface mapped(Surface original) {
        Entry e = entries.get(original);
        return e == null ? null : e.dummy;
    }

    synchronized void clear() {
        for (Entry e : entries.values()) e.close();
        entries.clear();
    }

    private static final class Entry {
        final SurfaceTexture texture;
        final Surface dummy;
        final LocalH264Decoder decoder;

        Entry(
                SurfaceTexture texture,
                Surface dummy,
                LocalH264Decoder decoder) {
            this.texture = texture;
            this.dummy = dummy;
            this.decoder = decoder;
        }

        void close() {
            decoder.close();
            try { dummy.release(); } catch (Throwable ignored) {}
            try { texture.release(); } catch (Throwable ignored) {}
        }
    }
}
