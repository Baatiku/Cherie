from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

logger = logging.getLogger("omnilive.webrtc")

try:
    # aiortc encodes the returned video at 1 Mbps (H264) / 500 kbps (VP8)
    # by default, which alone makes 720p look soft and blocky no matter
    # how good the swap is. Raise the starting point; the built-in
    # receiver-driven adaptation can still turn it down on congestion.
    from aiortc.codecs import h264 as _aiortc_h264
    from aiortc.codecs import vpx as _aiortc_vpx

    _aiortc_h264.DEFAULT_BITRATE = 2_500_000
    _aiortc_vpx.DEFAULT_BITRATE = 1_200_000

    # Ensure H264 software encoder is tuned strictly for zero latency (no B-frames, ultrafast preset)
    if hasattr(_aiortc_h264, "H264Encoder"):
        _orig_h264_encoder_init = _aiortc_h264.H264Encoder.__init__

        def _patched_h264_encoder_init(self, *args, **kwargs):
            _orig_h264_encoder_init(self, *args, **kwargs)
            if hasattr(self, "codec") and self.codec is not None:
                try:
                    if hasattr(self.codec, "options") and isinstance(self.codec.options, dict):
                        self.codec.options.update({"tune": "zerolatency", "preset": "ultrafast"})
                except Exception:
                    pass

        _aiortc_h264.H264Encoder.__init__ = _patched_h264_encoder_init
except Exception as exc:
    logger.warning("aiortc bitrate and latency defaults could not be configured: %s", exc)

from .frame_correlation import FrameCorrelator
from .ice import current_ice_config
from .models import WebRtcAnswer, WebRtcOffer
from .processor import processor
from .session import registry
from .telemetry import input_age_window, input_overwrite_counter, media_wait_window

_peers: dict[str, RTCPeerConnection] = {}
_return_tracks: dict[str, "LowLatencyVideoTrack"] = {}


def _rtp_timestamp(frame) -> int | None:
    if frame.pts is None:
        return None
    return int(frame.pts) & 0xFFFFFFFF


class FrameTimingBridge:
    def __init__(self, publish: Callable[[dict], None]):
        self.correlator = FrameCorrelator()
        self.publish = publish

    def _emit(self, reports: list[dict]) -> None:
        for report in reports:
            self.publish(report)

    def note_capture(
        self,
        sequence: int,
        capture_timestamp_ns: int,
        capture_elapsed_ns: int,
        rtp_guess: int,
    ) -> None:
        self._emit(
            self.correlator.note_capture(
                sequence,
                capture_timestamp_ns,
                capture_elapsed_ns,
                rtp_guess,
                time.perf_counter_ns(),
            )
        )

    def note_received(self, frame, received_ns: int) -> None:
        rtp = _rtp_timestamp(frame)
        if rtp is not None:
            self._emit(self.correlator.note_frame_received(rtp, received_ns))

    def note_processed(self, frame, processor_started_ns: int, processor_ended_ns: int) -> None:
        rtp = _rtp_timestamp(frame)
        if rtp is not None:
            self._emit(self.correlator.note_processed(rtp, processor_started_ns, processor_ended_ns))


class LatestFrameSource:
    """Continuously drains aiortc and retains only the freshest decoded frame."""

    def __init__(self, source: MediaStreamTrack, timing: FrameTimingBridge | None = None):
        self.source = source
        self.timing = timing
        self._condition = asyncio.Condition()
        self._latest = None
        self._latest_sequence = 0
        self._delivered_sequence = 0
        self._closed = False
        self._task = asyncio.create_task(self._pump(), name="omnilive-latest-frame")

    async def _pump(self) -> None:
        try:
            while True:
                frame = await self.source.recv()
                received_at = time.perf_counter_ns()
                if self.timing is not None:
                    self.timing.note_received(frame, received_at)
                async with self._condition:
                    if self._latest_sequence > self._delivered_sequence:
                        input_overwrite_counter.increment()
                    self._latest_sequence += 1
                    self._latest = (self._latest_sequence, frame, received_at)
                    self._condition.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never silently drop the input pump: surface it so /health
            # and logs show why a live track went dark.
            logger.warning("latest-frame pump ended: %s", exc)
        finally:
            async with self._condition:
                self._closed = True
                self._condition.notify_all()

    async def next(self, after_sequence: int):
        async with self._condition:
            await self._condition.wait_for(lambda: self._latest_sequence > after_sequence or self._closed)
            if self._latest_sequence <= after_sequence or self._latest is None:
                raise MediaStreamError
            self._delivered_sequence = self._latest_sequence
            return self._latest

    def close(self) -> None:
        self._closed = True
        self._task.cancel()


class LowLatencyVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, source: MediaStreamTrack, state, timing: FrameTimingBridge | None = None):
        super().__init__()
        self.source = source
        self.state = state
        self.stream_key = state.session_id
        self.timing = timing
        self.latest = LatestFrameSource(source, timing)
        self.sequence = 0
        self._closed = False

    async def recv(self):
        wait_started = time.perf_counter_ns()
        sequence, frame, received_at = await self.latest.next(self.sequence)
        processor_started = time.perf_counter_ns()
        media_wait_window.add((processor_started - wait_started) / 1_000_000.0)
        input_age_window.add((processor_started - received_at) / 1_000_000.0)
        self.sequence = sequence
        result = await processor.process(
            frame,
            self.state.effect,
            stream_key=self.stream_key,
            profile=self.state.profile,
        )
        processor_ended = time.perf_counter_ns()
        if self.timing is not None:
            self.timing.note_processed(frame, processor_started, processor_ended)
        return result

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.latest.close()
        processor.release_stream(self.stream_key)
        super().stop()


def peer_configuration() -> RTCConfiguration:
    config = current_ice_config()
    return RTCConfiguration(
        iceServers=[
            RTCIceServer(
                urls=server.urls,
                username=server.username or None,
                credential=server.credential or None,
            )
            for server in config.servers
        ]
    )


async def close_peer(session_id: str) -> None:
    track = _return_tracks.pop(session_id, None)
    pc = _peers.pop(session_id, None)
    if track is not None:
        track.stop()
    if pc is not None and pc.connectionState != "closed":
        # Yield one event-loop tick before closing the peer connection.
        # aioice queues STUN retry callbacks via loop.call_later(); if we
        # close the UDP socket synchronously they fire on a None transport
        # and crash with AttributeError. A single yield lets those callbacks
        # observe the closed state and bail cleanly.
        await asyncio.sleep(0)
        try:
            await asyncio.wait_for(pc.close(), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("peer close timed out or failed: %s", exc)


async def accept_offer(body: WebRtcOffer, timing_bridge: FrameTimingBridge | None = None) -> WebRtcAnswer:
    state = registry.authenticate(body.session_id, body.token)
    if state is None:
        raise PermissionError("invalid or expired session")

    await close_peer(state.session_id)
    pc = RTCPeerConnection(configuration=peer_configuration())
    _peers[state.session_id] = pc

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            returned_track = LowLatencyVideoTrack(track, state, timing_bridge)
            _return_tracks[state.session_id] = returned_track
            pc.addTrack(returned_track)

        @track.on("ended")
        async def on_ended():
            await close_peer(state.session_id)

    @pc.on("connectionstatechange")
    async def on_connection_state_change():
        # "disconnected" is a *transient* ICE state — the browser/Android client
        # will retry and typically reconnects within a few seconds on mobile.
        # Closing immediately on "disconnected" kills the session on every
        # brief network hiccup. Only clean up on truly terminal states.
        if pc.connectionState in {"failed", "closed"}:
            await close_peer(state.session_id)
        elif pc.connectionState == "disconnected":
            # Give the ICE agent 15 s to self-heal (normal on mobile handoffs).
            # If the state is still disconnected (or worse) after the grace
            # period, tear down. Using a task so we don't block the event.
            async def _deferred_close():
                await asyncio.sleep(15.0)
                if pc.connectionState in {"disconnected", "failed"}:
                    logger.info("peer %s still disconnected after grace period, closing", state.session_id)
                    await close_peer(state.session_id)
            asyncio.ensure_future(_deferred_close())

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=body.sdp, type=body.type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return WebRtcAnswer(sdp=pc.localDescription.sdp, type=pc.localDescription.type)
    except Exception:
        await close_peer(state.session_id)
        raise


async def close_all_peers() -> None:
    session_ids = list(_peers)
    await asyncio.gather(*(close_peer(session_id) for session_id in session_ids), return_exceptions=True)
