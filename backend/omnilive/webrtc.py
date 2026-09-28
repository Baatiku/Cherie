from __future__ import annotations

import asyncio
import time

from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from .models import WebRtcAnswer, WebRtcOffer
from .processor import processor
from .session import registry
from .telemetry import input_age_window, input_overwrite_counter, media_wait_window

_peers: set[RTCPeerConnection] = set()


class LatestFrameSource:
    """Continuously drains aiortc and retains only the freshest decoded frame."""

    def __init__(self, source: MediaStreamTrack):
        self.source = source
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
                received_at = time.perf_counter()
                async with self._condition:
                    if self._latest_sequence > self._delivered_sequence:
                        input_overwrite_counter.increment()
                    self._latest_sequence += 1
                    self._latest = (self._latest_sequence, frame, received_at)
                    self._condition.notify_all()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
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

    def __init__(self, source: MediaStreamTrack, state):
        super().__init__()
        self.source = source
        self.state = state
        self.stream_key = state.session_id
        self.latest = LatestFrameSource(source)
        self.sequence = 0

    async def recv(self):
        wait_started = time.perf_counter()
        sequence, frame, received_at = await self.latest.next(self.sequence)
        media_wait_window.add((time.perf_counter() - wait_started) * 1000.0)
        input_age_window.add((time.perf_counter() - received_at) * 1000.0)
        self.sequence = sequence
        return await processor.process(
            frame,
            self.state.effect,
            stream_key=self.stream_key,
            profile=self.state.profile,
        )

    def stop(self) -> None:
        self.latest.close()
        processor.release_stream(self.stream_key)
        super().stop()


async def accept_offer(body: WebRtcOffer) -> WebRtcAnswer:
    state = registry.authenticate(body.session_id, body.token)
    if state is None:
        raise PermissionError("invalid or expired session")

    pc = RTCPeerConnection()
    _peers.add(pc)
    returned_track: LowLatencyVideoTrack | None = None

    @pc.on("track")
    def on_track(track):
        nonlocal returned_track
        if track.kind == "video":
            returned_track = LowLatencyVideoTrack(track, state)
            pc.addTrack(returned_track)

        @track.on("ended")
        async def on_ended():
            if returned_track is not None:
                returned_track.stop()
            await pc.close()
            _peers.discard(pc)

    @pc.on("connectionstatechange")
    async def on_connection_state_change():
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            if returned_track is not None:
                returned_track.stop()
            await pc.close()
            _peers.discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=body.sdp, type=body.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return WebRtcAnswer(sdp=pc.localDescription.sdp, type=pc.localDescription.type)


async def close_all_peers() -> None:
    peers = list(_peers)
    _peers.clear()
    await asyncio.gather(*(pc.close() for pc in peers), return_exceptions=True)
