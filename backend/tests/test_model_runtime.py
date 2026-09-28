from pathlib import Path
from omnilive.model_runtime import OnnxRuntime

def test_runtime_is_lazy_when_no_model(monkeypatch):
    from omnilive import model_runtime
    monkeypatch.setattr(model_runtime.settings,"model_id","none",raising=False)
    runtime=OnnxRuntime()
    assert runtime.load() is None
    assert runtime.loaded is False
