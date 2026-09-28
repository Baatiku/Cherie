from __future__ import annotations
import asyncio
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from .models import WebRtcAnswer, WebRtcOffer
from .session import registry

_peers: set[RTCPeerConnection] = set()

class LowLatencyVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, source: MediaStreamTrack):
        super().__init__()
        self.source = source
        self.sequence = 0
    async def recv(self):
        frame = await self.source.recv()
        self.sequence += 1
        return frame

async def accept_offer(body: WebRtcOffer) -> WebRtcAnswer:
    if registry.authenticate(body.session_id, body.token) is None:
        raise PermissionError("invalid or expired session")

    pc = RTCPeerConnection()
    _peers.add(pc)

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            pc.addTrack(LowLatencyVideoTrack(track))

        @track.on("ended")
        async def on_ended():
            await pc.close()
            _peers.discard(pc)

    @pc.on("connectionstatechange")
    async def on_connection_state_change():
        if pc.connectionState in {"failed", "closed", "disconnected"}:
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
