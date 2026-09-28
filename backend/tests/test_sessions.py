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
