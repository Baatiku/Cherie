# Way 2 test — PC viewer + virtual camera for Zoom / Meet / Teams

No root needed. Your PC webcam goes to the Modal GPU and comes back
to a browser tab. Zoom picks up that tab through OBS Virtual Camera.

## 1) Open the viewer

Open `index.html` in Chrome or Edge on your PC (double-click works,
no server needed). Or serve it:

```text
cd tools/pc-viewer
python -m http.server 8080
```

Then open `http://localhost:8080/index.html`.

## 2) Start a live swap

1. Backend URL is prefilled with your Modal app.
2. Pick a clear front-facing reference photo.
3. Press **Start live swap** and allow the camera.
4. Right video = swapped output. Make it fullscreen.

## 3) Feed it to Zoom / Meet / Teams

1. Install OBS Studio (free) on the same PC.
2. OBS Sources -> Add -> Window Capture -> pick this browser tab.
3. OBS -> Start Virtual Camera.
4. In Zoom / Meet / Teams camera list, pick **OBS Virtual Camera**.

That camera now shows your swapped face. To go back to your real
face, pick your real webcam in the same list.

## Notes

- Backend effect is fixed to `face_swap` with your `hyper1c` default.
- If you see your real face, the reference photo was missing or
  rejected — restart with a clear photo.
- If video stalls, press Stop and Start again (Modal L4 cold start
  takes a few seconds on the first run).
