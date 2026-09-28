from types import SimpleNamespace
from omnilive.model_runtime import OnnxRuntime

def test_runtime_is_lazy_when_no_model(monkeypatch):
    import omnilive.model_runtime as model_runtime
    fake=SimpleNamespace(model_id="none",model_root="/models")
    monkeypatch.setattr(model_runtime,"settings",fake)
    runtime=OnnxRuntime()
    assert runtime.load() is None
    assert runtime.loaded is False
    assert runtime.status()["configured"] is False
