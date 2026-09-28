import time
from fastapi.testclient import TestClient
from omnilive.app import app

client=TestClient(app)

def test_session_and_frame_policy():
    created=client.post("/v1/sessions",json={"profile":"balanced","effect":"soft_focus"}).json()
    assert created["effect"]=="soft_focus"
    payload={
        "session_id":created["session_id"],
        "sequence":1,
        "captured_at_ms":int(time.time()*1000),
        "width":1280,
        "height":720,
    }
    r=client.post("/v1/frames/decision",json=payload,headers={"Authorization":"Bearer "+created["token"]})
    assert r.status_code==200
    assert r.json()["accept"] is True

def test_rejects_out_of_order_frame():
    created=client.post("/v1/sessions",json={}).json()
    headers={"Authorization":"Bearer "+created["token"]}
    base={"session_id":created["session_id"],"captured_at_ms":int(time.time()*1000),"width":640,"height":360}
    assert client.post("/v1/frames/decision",json={**base,"sequence":4},headers=headers).json()["accept"]
    result=client.post("/v1/frames/decision",json={**base,"sequence":3},headers=headers).json()
    assert result["accept"] is False
    assert result["reason"]=="out_of_order"


def test_session_stores_reference_image_in_worker_local_state():
    from omnilive.models import EffectProfile, QualityProfile
    from omnilive.reference_image import ValidatedReferenceImage
    from omnilive.session import registry

    created = registry.create(QualityProfile.BALANCED, EffectProfile.CUSTOM_ONNX, None)
    reference = ValidatedReferenceImage(
        jpeg_bytes=b"jpeg",
        width=128,
        height=96,
        sha256="a" * 64,
    )
    assert registry.set_reference_image(created.session_id, reference) is True
    stored = registry.get_reference_image(created.session_id)
    assert stored == reference
    assert registry.discard(created.session_id) is True
    assert registry.get_reference_image(created.session_id) is None
    assert registry.discard(created.session_id) is False
