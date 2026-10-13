"""OmniLive PC peer (Way 2, no browser, no OBS).

Laptop webcam -> Modal GPU face swap -> straight into the system virtual
camera that Teams / Meet / Zoom see. The browser page is not involved.

Needs (once):  pip install aiortc opencv-python pyvirtualcam websockets
Needs: OBS installed (its virtual-camera driver is the output device),
       even though OBS itself stays closed.

Run:
    python pc_peer.py --reference myface.jpg
    python pc_peer.py --reference myface.jpg --profile high --camera 0

Then in Teams / Meet / Zoom pick the camera named "OBS Virtual Camera".
Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import urllib.request
from fractions import Fraction

import cv2
import numpy as np

FPS = 30

BAKED_ICE = [
    {"urls": ["stun:stun.relay.metered.ca:80"]},
    {"urls": ["turn:global.relay.metered.ca:80"], "username": "ae3c22bd148853255a70cb99", "credential": "BFMymojsZRgOwHdo"},
    {"urls": ["turn:global.relay.metered.ca:80?transport=tcp"], "username": "ae3c22bd148853255a70cb99", "credential": "BFMymojsZRgOwHdo"},
    {"urls": ["turn:global.relay.metered.ca:443"], "username": "ae3c22bd148853255a70cb99", "credential": "BFMymojsZRgOwHdo"},
    {"urls": ["turns:global.relay.metered.ca:443?transport=tcp"], "username": "ae3c22bd148853255a70cb99", "credential": "BFMymojsZRgOwHdo"},
    {"urls": ["turn:openrelay.metered.ca:80"], "username": "openrelay", "credential": "openrelay"},
    {"urls": ["turn:openrelay.metered.ca:443"], "username": "openrelay", "credential": "openrelay"},
    {"urls": ["turn:openrelay.metered.ca:443?transport=tcp"], "username": "openrelay", "credential": "openrelay"},
]


def fetch_ice(backend: str) -> list[dict]:
    try:
        with urllib.request.urlopen(backend.rstrip("/") + "/v1/ice", timeout=10) as r:
            cfg = json.loads(r.read().decode("utf-8"))
        servers = [
            {"urls": s["urls"], "username": s.get("username") or None, "credential": s.get("credential") or None}
            for s in cfg.get("servers", [])
        ]
        return [s for s in servers if s["urls"]] or BAKED_ICE
    except Exception as exc:
        print(f"[ice] server list unreachable ({exc}), using built-in relays", flush=True)
        return BAKED_ICE


def load_reference(path: str) -> str:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise SystemExit(f"cannot read reference photo: {path}")
    h, w = img.shape[:2]
    scale = min(1.0, 1024.0 / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(32, int(w * scale)), max(32, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise SystemExit("reference photo convert failed")
    print(f"[ref] sending {len(buf) // 1024} KB", flush=True)
    return base64.b64encode(bytes(buf)).decode("ascii")


async def main() -> None:
    from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import VideoFrame
    import websockets

    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="https://baatiku--omnilive-api.modal.run")
    ap.add_argument("--profile", default="balanced", choices=["low_latency", "balanced", "high"])
    ap.add_argument("--reference", required=True, help="face photo to wear (jpg/png)")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--preview", action="store_true", help="show live preview window")
    args = ap.parse_args()


    ref_b64 = load_reference(args.reference)

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    ok, probe = await asyncio.to_thread(cap.read)
    if not ok:
        raise SystemExit("laptop camera did not open (try --camera 1)")

    class CameraTrack(MediaStreamTrack):
        kind = "video"

        def __init__(self) -> None:
            super().__init__()
            self._pts = 0

        async def recv(self) -> VideoFrame:
            ok, bgr = await asyncio.to_thread(cap.read)
            if not ok:
                await asyncio.sleep(1.0 / FPS)
                bgr = np.zeros((args.height, args.width, 3), dtype=np.uint8)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            frame = VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = self._pts
            frame.time_base = Fraction(1, 90000)
            self._pts += int(90000 / FPS)
            return frame

    ice = fetch_ice(args.backend)
    ice_servers = ice
    pc = None
    ws = None
    stop_all = asyncio.Event()

    vcam = None
    live_announced = False

    async def run_session(attempt: int) -> None:
        nonlocal pc, ws, vcam, live_announced
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[
            RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential")) for s in ice_servers
        ]))
        pc.addTrack(CameraTrack())

        @pc.on("track")
        def on_track(track):
            print("[net] return track arrived", flush=True)

            async def pump():
                nonlocal vcam, live_announced
                import pyvirtualcam

                while not stop_all.is_set():
                    try:
                        frame = await track.recv()
                    except Exception:
                        break
                    rgb = frame.to_ndarray(format="rgb24")
                    h, w = rgb.shape[:2]
                    if vcam is None or vcam.width != w or vcam.height != h:
                        if vcam is not None:
                            vcam.close()
                        vcam = pyvirtualcam.Camera(width=w, height=h, fps=FPS, print_fps=False)
                        print(f"[cam] virtual camera open ({w}x{h}) -> pick '{vcam.device}' in Teams/Meet/Zoom", flush=True)
                    vcam.send(rgb)
                    if args.preview:
                        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                        cv2.imshow("OmniLive Swapped Camera Preview (Q to close)", bgr)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            stop_all.set()
                            break
                    vcam.sleep_until_next_frame()
                    if not live_announced:
                        live_announced = True
                        print("[live] SWAPPED FACE IS ON THE VIRTUAL CAMERA", flush=True)

            asyncio.ensure_future(pump())


        await pc.setLocalDescription(await pc.createOffer())
        for _ in range(140):  # ~7 s gathering cap, same as the Android app
            if pc.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.05)
        sdp = pc.localDescription.sdp
        print(f"[net] dialling (try {attempt})...", flush=True)

        ws_url = args.backend.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/v1/live"

        async def pinger(sock) -> None:
            # The cloud drops quiet calls after 60 s. A ping every 20 s
            # keeps long meetings alive.
            try:
                while not stop_all.is_set():
                    await asyncio.sleep(20)
                    await sock.send(json.dumps({"action": "ping"}))
            except (asyncio.CancelledError, Exception):
                pass

        async with websockets.connect(ws_url, max_size=8_000_000) as sock:
            ws = sock
            ping_task = asyncio.ensure_future(pinger(sock))
            try:
                await sock.send(json.dumps({"action": "start", "profile": args.profile,
                                          "effect": "face_swap", "sdp": sdp, "type": "offer"}))
                answered = False
                async for raw in sock:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    ev = msg.get("event")
                    if ev == "answer" and not answered:
                        answered = True
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))
                        print("[net] connected, sending face photo...", flush=True)
                        await sock.send(json.dumps({"action": "reference_image", "mime_type": "image/jpeg",
                                                  "data_base64": ref_b64}))
                    elif ev == "reference_image" and msg.get("status") == "ready":
                        print("[live] identity ready", flush=True)
                    elif ev == "reference_image":
                        print(f"[ref] rejected: {msg.get('detail', 'unknown')}", flush=True)
                    elif ev == "pong":
                        pass
                    elif ev == "error":
                        print(f"[server] {msg.get('detail', 'unknown')} -> reconnecting...", flush=True)
                        break
            finally:
                ping_task.cancel()
                ws = None
                try:
                    await pc.close()
                except Exception:
                    pass
                pc = None

    attempt = 0
    try:
        while not stop_all.is_set():
            attempt += 1
            try:
                await run_session(attempt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[net] connection failed: {exc} -> retrying...", flush=True)
            if not stop_all.is_set():
                await asyncio.sleep(3)
    except asyncio.CancelledError:
        raise
    finally:
        stop_all.set()
        cap.release()
        if vcam is not None:
            vcam.close()
        if args.preview:
            cv2.destroyAllWindows()



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
        sys.exit(0)
