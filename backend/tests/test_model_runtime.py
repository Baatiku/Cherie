from types import SimpleNamespace

import numpy as np
import pytest

from omnilive.model_runtime import OnnxRuntime


def test_runtime_is_lazy_when_no_model(monkeypatch):
    import omnilive.model_runtime as model_runtime

    fake=SimpleNamespace(
        model_id="none",
        model_root="/models",
        model_adapter="single_image",
        model_live_input="",
        model_reference_input="",
    )
    monkeypatch.setattr(model_runtime,"settings",fake)
    runtime=OnnxRuntime()
    assert runtime.load() is None
    assert runtime.loaded is False
    assert runtime.status()["configured"] is False
    assert runtime.status()["adapter"] == "single_image"


def test_dual_image_adapter_resolves_two_inputs(monkeypatch):
    import omnilive.model_runtime as model_runtime

    fake=SimpleNamespace(
        model_adapter="dual_image",
        model_live_input="live",
        model_reference_input="reference",
    )
    monkeypatch.setattr(model_runtime,"settings",fake)
    runtime=OnnxRuntime()
    live=SimpleNamespace(name="live",shape=[1,3,64,64],type="tensor(float)")
    reference=SimpleNamespace(name="reference",shape=[1,3,112,112],type="tensor(float)")
    session=SimpleNamespace(get_inputs=lambda:[reference,live])
    selected_live,selected_reference=runtime._select_input_roles(session)
    assert selected_live.name=="live"
    assert selected_reference.name=="reference"
    assert runtime.requires_reference is True


def test_dual_image_adapter_requires_exactly_two_inputs(monkeypatch):
    import omnilive.model_runtime as model_runtime

    fake=SimpleNamespace(model_adapter="dual_image",model_live_input="",model_reference_input="")
    monkeypatch.setattr(model_runtime,"settings",fake)
    runtime=OnnxRuntime()
    session=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name="only",shape=[1,3,64,64])])
    with pytest.raises(RuntimeError,match="exactly two"):
        runtime._select_input_roles(session)


def test_prepare_image_supports_nchw_and_nhwc():
    rgb=np.zeros((40,60,3),dtype=np.uint8)
    nchw=SimpleNamespace(shape=[1,3,32,48])
    nhwc=SimpleNamespace(shape=[1,24,36,3])
    a,a_is_nchw=OnnxRuntime._prepare_image(rgb,nchw)
    b,b_is_nchw=OnnxRuntime._prepare_image(rgb,nhwc)
    assert a.shape==(1,3,32,48)
    assert a_is_nchw is True
    assert b.shape==(1,24,36,3)
    assert b_is_nchw is False


def test_transient_gpu_failure_reloads_on_next_provider(monkeypatch):
    import omnilive.model_runtime as model_runtime

    fake=SimpleNamespace(
        model_id="fake.onnx",
        model_root="/models",
        model_adapter="single_image",
        model_live_input="",
        model_reference_input="",
        model_allow_cpu_fallback=True,
        model_retry_cooldown_seconds=0.0,
        model_provider_quarantine_seconds=30.0,
    )
    monkeypatch.setattr(model_runtime,"settings",fake)

    meta=SimpleNamespace(name="live",shape=[1,3,8,8],type="tensor(float)")

    class FailingSession:
        def get_inputs(self):
            return [meta]

        def get_providers(self):
            return ["CUDAExecutionProvider","CPUExecutionProvider"]

        def run(self,*_args,**_kwargs):
            raise RuntimeError("CUDAExecutionProvider failed to allocate GPU memory")

    class RecoveredSession:
        def get_inputs(self):
            return [meta]

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def run(self,*_args,**_kwargs):
            return [np.zeros((1,3,8,8),dtype=np.float32)]

    runtime=OnnxRuntime()
    sessions=[FailingSession(),RecoveredSession()]
    load_calls=[]

    def fake_load(strict=False):
        load_calls.append(strict)
        session=sessions.pop(0)
        runtime._session=session
        runtime._providers=list(session.get_providers())
        return session

    monkeypatch.setattr(runtime,"load",fake_load)

    rgb=np.zeros((8,8,3),dtype=np.uint8)
    output=runtime.run_image(rgb)

    assert output.shape==rgb.shape
    assert len(load_calls)==2
    status=runtime.status()
    assert status["execution_failure_count"]==1
    assert status["recovery_count"]==1
    assert status["consecutive_failures"]==0
    assert status["providers"]==["CPUExecutionProvider"]
    assert status["quarantined_providers"]==["CUDAExecutionProvider"]


def test_non_provider_model_error_does_not_retry(monkeypatch):
    import omnilive.model_runtime as model_runtime

    fake=SimpleNamespace(
        model_id="fake.onnx",
        model_root="/models",
        model_adapter="single_image",
        model_live_input="",
        model_reference_input="",
        model_allow_cpu_fallback=False,
        model_retry_cooldown_seconds=0.0,
        model_provider_quarantine_seconds=30.0,
    )
    monkeypatch.setattr(model_runtime,"settings",fake)

    meta=SimpleNamespace(name="live",shape=[1,3,8,8],type="tensor(float)")

    class InvalidSession:
        def get_inputs(self):
            return [meta]

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def run(self,*_args,**_kwargs):
            raise RuntimeError("invalid input dimensions")

    runtime=OnnxRuntime()
    session=InvalidSession()
    calls=[]

    def fake_load(strict=False):
        calls.append(strict)
        runtime._session=session
        runtime._providers=list(session.get_providers())
        return session

    monkeypatch.setattr(runtime,"load",fake_load)

    with pytest.raises(RuntimeError,match="invalid input dimensions"):
        runtime.run_image(np.zeros((8,8,3),dtype=np.uint8))

    assert len(calls)==1
    status=runtime.status()
    assert status["execution_failure_count"]==1
    assert status["recovery_count"]==0
    assert status["quarantined_providers"]==[]
