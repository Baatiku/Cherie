from omnilive.models import EffectProfile, QualityProfile
from omnilive.session import registry


def test_active_session_profile_can_follow_adaptive_capture():
    created = registry.create(QualityProfile.HIGH, EffectProfile.ORIGINAL, None)
    state = registry.authenticate(created.session_id, created.token)
    assert state is not None
    assert state.profile == QualityProfile.HIGH
    assert registry.update_profile(created.session_id, QualityProfile.LOW_LATENCY)
    assert state.profile == QualityProfile.LOW_LATENCY
