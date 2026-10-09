# PC peer — swapped face straight into Teams / Meet / Zoom

No browser. No OBS setup. This script sends your laptop camera to the
Modal GPU and puts the swapped face directly on the **OBS Virtual
Camera** device, which Teams / Meet / Zoom list as a normal webcam.

## Once

```text
pip install aiortc opencv-python pyvirtualcam websockets
```

Keep OBS installed (its camera driver is the output), but OBS stays closed.

## Run

```text
cd tools/pc-peer
python pc_peer.py --reference myface.jpg
```

Use a clear front-facing photo. Wait for:

```text
[live] SWAPPED FACE IS ON THE VIRTUAL CAMERA
```

Then in Teams / Meet / Zoom pick **OBS Virtual Camera**.
Press Ctrl+C to stop.

## Options

```text
python pc_peer.py --reference myface.jpg --profile high
python pc_peer.py --reference myface.jpg --camera 1   # second webcam
```

Profiles: `low_latency` (fastest), `balanced`, `high` (sharpest).
Cost is the same per-minute GPU billing as the browser test: A10G
is about $1.10 per hour while connected, $0 when stopped.
