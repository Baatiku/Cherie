from omnilive.webrtc import peer_configuration


def test_backend_peer_uses_dynamic_ice_configuration():
    config = peer_configuration()
    assert config.iceServers
    assert config.iceServers[0].urls
