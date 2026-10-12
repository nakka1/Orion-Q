"""Global span inventories retain exact static calibration across revisits."""
from dataclasses import FrozenInstanceError
import pytest
from quantum_autopilot.control_plane import keyrate_loop as kr

pytestmark = pytest.mark.unit


def test_global_working_set_does_not_recompute_or_share_mutable_settings():
    kr.commissioned.cache_clear()
    try:
        spans = [float(n) for n in range(25, 49)]
        first = [kr.commissioned(span) for span in spans]
        initial = kr.commissioned.cache_info()
        second = [kr.commissioned(span) for span in spans]
        final = kr.commissioned.cache_info()
        assert first == second
        assert final.misses == initial.misses == len(spans)
        assert final.hits - initial.hits == len(spans)
        with pytest.raises(FrozenInstanceError):
            first[0].signal_intensity = 0.0
    finally:
        kr.commissioned.cache_clear()
