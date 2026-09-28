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
