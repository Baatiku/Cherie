from omnilive.temporal import AdaptiveInferenceBudget, profile_defaults


def test_budget_sheds_load_quickly():
    budget = AdaptiveInferenceBudget(profile_defaults("balanced"))
    original_fps = budget.target_neural_fps
    for _ in range(4):
        budget.observe_inference(100.0)
    assert budget.target_neural_fps < original_fps
    assert budget.detector_interval > profile_defaults("balanced").detector_interval
    assert budget.crop_max_side < profile_defaults("balanced").crop_max_side
    assert budget.overload_events >= 1


def test_budget_recovery_never_exceeds_profile():
    budget = AdaptiveInferenceBudget(profile_defaults("low_latency"))
    for _ in range(4):
        budget.observe_inference(200.0)
    for _ in range(100):
        budget.observe_inference(1.0)
    base = profile_defaults("low_latency")
    assert budget.target_neural_fps <= base.neural_fps
    assert budget.detector_interval >= base.detector_interval
    assert budget.crop_max_side <= base.crop_max_side
