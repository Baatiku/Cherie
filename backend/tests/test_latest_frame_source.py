import asyncio

import pytest
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

from omnilive.webrtc import LatestFrameSource


class BlockingVideoTrack(MediaStreamTrack):
    kind = "video"

    async def recv(self):
        await asyncio.Event().wait()


def test_latest_frame_close_unblocks_waiter():
    async def scenario():
        latest = LatestFrameSource(BlockingVideoTrack())
        waiter = asyncio.create_task(latest.next(0))
        await asyncio.sleep(0)
        latest.close()
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(waiter, timeout=0.5)

    asyncio.run(scenario())
