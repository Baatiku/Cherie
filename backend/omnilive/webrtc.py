from __future__ import annotations

import asyncio
import time

from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from .ice import current_ice_config
from .models import WebRtcAnswer, WebRtcOffer
from .processor import processor
from .session import registry
from .telemetry import input_age_window, input_overwrite_counter, media_wait_window

_peers: dict[str, RTCPeerConnection] = {}
_return_tracks: dict[str, "LowLatencyVideoTrack"] = {}


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
        self._closed = False

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
        await pc.close()


async def accept_offer(body: WebRtcOffer) -> WebRtcAnswer:
    state = registry.authenticate(body.session_id, body.token)
    if state is None:
        raise PermissionError("invalid or expired session")

    await close_peer(state.session_id)
    pc = RTCPeerConnection(configuration=peer_configuration())
    _peers[state.session_id] = pc

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            returned_track = LowLatencyVideoTrack(track, state)
            _return_tracks[state.session_id] = returned_track
            pc.addTrack(returned_track)

        @track.on("ended")
        async def on_ended():
            await close_peer(state.session_id)

    @pc.on("connectionstatechange")
    async def on_connection_state_change():
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            await close_peer(state.session_id)

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
