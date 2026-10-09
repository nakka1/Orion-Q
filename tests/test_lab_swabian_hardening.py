"""Synthetic SDK contract doubles; no instrument or licensed native replay used."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quantum_autopilot.lab.adapters import SwabianSDKAdapter
from quantum_autopilot.lab.photon import AcquisitionError, PhotonConfig


def connected_native(*, ranges=None, reject_second=False):
    adapter = SwabianSDKAdapter(event_type_map={7: "TimeTag"}, test_only_transport=True)
    applied = {1: .35, 2: .35}
    delays = {1: 0, 2: -125}
    writes = []

    def trigger(channel, value):
        writes.append(("trigger", channel, value))
        if reject_second and channel == 2:
            raise RuntimeError("synthetic second channel rejected")
        applied[channel] = value

    adapter.tagger = SimpleNamespace(
        getChannelList=lambda: [1, 2, 3],
        getTriggerLevelRange=lambda c: (ranges or {}).get(c, (-2.5, 2.5)),
        setTriggerLevel=trigger, getTriggerLevel=lambda c: applied[c],
        setInputDelay=lambda c, value: (writes.append(("delay", c, value)), delays.__setitem__(c, value)),
        getInputDelay=lambda c: delays[c],
        sync=lambda **kwargs: True, getConfiguration=lambda: {"fixture": True},
        autoCalibration=lambda: [10., 10.],
        getReferenceClockState=lambda: SimpleNamespace(enabled=False),
        disableReferenceClock=lambda: writes.append(("disable_reference",)),
    )
    adapter._clock_domain = "synthetic-clock"
    adapter.configuration = replace(PhotonConfig(), clock_domain=adapter._clock_domain)
    adapter.state = "ready"
    return adapter, applied, delays, writes


def test_all_native_ranges_are_validated_before_any_write():
    adapter, applied, _, writes = connected_native(ranges={2: (0, .4)})
    old_id = adapter.configuration.configuration_id
    with pytest.raises(AcquisitionError, match="range"):
        adapter.configure(replace(PhotonConfig(), trigger_levels_v=(.5, .5)))
    assert writes == []
    assert applied == {1: .35, 2: .35}
    assert adapter.state == "ready" and adapter.configuration.configuration_id == old_id


def test_partial_application_blocks_acquisition_and_clears_stale_configuration():
    adapter, applied, _, _ = connected_native(reject_second=True)
    requested = replace(PhotonConfig(), trigger_levels_v=(.5, .5))
    with pytest.raises(AcquisitionError, match="unverified"):
        adapter.configure(requested)
    assert applied == {1: .5, 2: .35}
    assert adapter.state == "configuration_unverified"
    assert adapter.configuration is None
    with pytest.raises(AcquisitionError, match="configuration"):
        adapter.acquire("must-block-before-stream")
    with pytest.raises(AcquisitionError, match="mismatch"):
        adapter.reverify_configuration()
    assert adapter.state == "configuration_unverified"
    # Explicitly acknowledge an independently verified applied mixed configuration.
    result = adapter.reverify_configuration(replace(requested, trigger_levels_v=(.5, .35)))
    assert result["readback_verified"] is True
    assert adapter.state == "configured"
    with pytest.raises(AcquisitionError):
        adapter.acquire("calibration-still-required")
    adapter.calibrate()
    assert adapter.state == "ready"


@pytest.mark.parametrize("changes", [
    {"channels": (1, 99)}, {"reference_channel": 99},
    {"input_delays_ps": (2**63, -125)}, {"timestamp_unit": "ns"},
    {"trigger_levels_v": (.5, float("nan"))},
])
def test_invalid_batch_performs_no_setters(changes):
    adapter, _, _, writes = connected_native()
    with pytest.raises(AcquisitionError):
        adapter.configure(replace(PhotonConfig(), **changes))
    assert writes == [] and adapter.state == "ready"


@pytest.mark.parametrize("failure", ["delay", "sync", "readback", "reference"])
def test_native_apply_failure_at_each_stage_revokes_acquisition(failure):
    adapter, _, _, _ = connected_native()
    def reject(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    method = {"delay": "setInputDelay", "sync": "sync", "readback": "getTriggerLevel",
              "reference": "disableReferenceClock"}[failure]
    setattr(adapter.tagger, method, reject)
    with pytest.raises(AcquisitionError, match="unverified"):
        adapter.configure(PhotonConfig())
    assert adapter.configuration is None and adapter.state == "configuration_unverified"
    assert adapter.identify()["acquisition_blocked"]


def test_readback_mismatch_after_successful_setter_blocks_and_does_not_accept_unrequested_values():
    adapter, _, _, _ = connected_native()
    adapter.tagger.getTriggerLevel = lambda c: .49
    with pytest.raises(AcquisitionError, match="unverified"):
        adapter.configure(replace(PhotonConfig(), trigger_levels_v=(.5, .5)))
    assert adapter.state == "configuration_unverified"
    assert adapter._last_readback["trigger_levels_v"] == [.49, .49]
    with pytest.raises(AcquisitionError, match="mismatch"):
        adapter.reverify_configuration()


def test_external_configuration_change_is_detected_before_acquiring():
    adapter, applied, _, _ = connected_native()
    adapter.configure(PhotonConfig())
    adapter.calibrate()
    applied[1] = .5
    with pytest.raises(AcquisitionError, match="unverified"):
        adapter.acquire("external-change")
    assert adapter.configuration is None and adapter.state == "configuration_unverified"


def test_concurrent_native_configuration_cannot_pass_a_ready_state_check():
    import threading
    adapter, _, _, _ = connected_native()
    entered, released = threading.Event(), threading.Event()
    original = adapter.tagger.setTriggerLevel
    def blocking(channel, value):
        entered.set()
        assert released.wait(5)
        original(channel, value)
    adapter.tagger.setTriggerLevel = blocking
    thread = threading.Thread(target=lambda: adapter.configure(PhotonConfig()))
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(AcquisitionError, match="operation in progress"):
            adapter.acquire("concurrent")
        with pytest.raises(AcquisitionError, match="operation in progress"):
            adapter.configure(PhotonConfig())
        with pytest.raises(AcquisitionError, match="operation in progress"):
            adapter.disconnect()
    finally:
        released.set()
        thread.join(5)
    assert adapter.state == "configured"


def test_native_continuous_drains_each_sdk_buffer_via_same_durable_contract(tmp_path):
    adapter, _, _, _ = connected_native()
    adapter._identity = {"serial": "synthetic-interface-double", "device_id": "synthetic-sdk-fixture"}
    adapter.tagger.clearOverflows = lambda: None
    buffers = [SimpleNamespace(getTimestamps=lambda i=i: [1000*i+100, 1000*i+101],
                               getChannels=lambda: [1, 2], getEventTypes=lambda: [7, 7],
                               getMissedEvents=lambda: [0, 0], hasOverflows=False,
                               size=2, tStart=0, tGetData=1000*(i+1)) for i in range(3)]
    empty = SimpleNamespace(getTimestamps=lambda: [], getChannels=lambda: [], getEventTypes=lambda: [],
                            getMissedEvents=lambda: [], hasOverflows=False, size=0, tStart=0, tGetData=3000)
    calls = []
    stream = SimpleNamespace(startFor=lambda duration: calls.append(("start", duration)),
                             getData=lambda: buffers.pop(0) if buffers else empty,
                             isRunning=lambda: bool(buffers), stop=lambda: calls.append(("stop",)))
    adapter.sdk = SimpleNamespace(TimeTagStream=lambda *args: stream)
    adapter.configure(PhotonConfig())
    adapter.calibrate()
    summary = adapter.acquire_continuous("synthetic-native-stream", tmp_path / "native.sqlite", drain_interval_s=.001)
    assert summary["blocks"] == 3 and summary["received_events"] == 6
    assert summary["produced_events"] is None and not summary["loss_count_exact"]
    assert adapter.state == "ready" and calls[0][0] == "start" and calls[-1] == ("stop",)


def test_cancelled_native_buffer_is_drained_after_stop_before_return(tmp_path):
    import threading
    adapter, _, _, _ = connected_native()
    adapter._identity = {"serial": "synthetic-interface-double", "device_id": "synthetic-sdk-fixture"}
    adapter.tagger.clearOverflows = lambda: None
    pending = [SimpleNamespace(getTimestamps=lambda: [100, 101], getChannels=lambda: [1, 2],
                               getEventTypes=lambda: [7, 7], getMissedEvents=lambda: [0, 0],
                               hasOverflows=False, size=2, tStart=0, tGetData=1000)]
    empty = SimpleNamespace(getTimestamps=lambda: [], getChannels=lambda: [], getEventTypes=lambda: [],
                            getMissedEvents=lambda: [], hasOverflows=False, size=0, tStart=0, tGetData=1000)
    stream = SimpleNamespace(startFor=lambda duration: None, getData=lambda: pending.pop() if pending else empty,
                             isRunning=lambda: True, stop=lambda: None)
    adapter.sdk = SimpleNamespace(TimeTagStream=lambda *args: stream)
    adapter.configure(PhotonConfig())
    adapter.calibrate()
    cancel = threading.Event(); cancel.set()
    summary = adapter.acquire_continuous("synthetic-cancelled-native", tmp_path / "cancel-native.sqlite", cancel=cancel)
    assert summary["status"] == "cancelled" and summary["blocks"] == 1 and summary["received_events"] == 2


def test_native_continuous_security_revocation_stops_before_next_drain(tmp_path):
    from quantum_autopilot.lab.capture import CaptureJournal
    adapter, _, _, _ = connected_native()
    adapter._identity = {"serial": "synthetic-interface-double", "device_id": "synthetic-sdk-fixture"}
    adapter.tagger.clearOverflows = lambda: None
    pending = [SimpleNamespace(getTimestamps=lambda: [100, 101], getChannels=lambda: [1, 2],
        getEventTypes=lambda: [7, 7], getMissedEvents=lambda: [0, 0], hasOverflows=False,
        size=2, tStart=0, tGetData=1000)]
    stream = SimpleNamespace(startFor=lambda _: None, getData=lambda: pending.pop(),
                             isRunning=lambda: True, stop=lambda: None)
    adapter.sdk = SimpleNamespace(TimeTagStream=lambda *args: stream)
    adapter.configure(PhotonConfig()); adapter.calibrate()
    # This trusted double exercises the production guard without producing a
    # physical event: the first drain is refused before getData can be called.
    adapter._test_only_transport = False
    calls = []
    def guard(**kwargs):
        calls.append(kwargs)
        if len(calls) >= 3:
            raise AcquisitionError("fixture authenticated session revoked")
    adapter._security_guard = guard
    path = tmp_path / "revoked.sqlite"
    with pytest.raises(AcquisitionError, match="session revoked"):
        adapter.acquire_continuous("revoked-before-drain", path, drain_interval_s=.001)
    summary = CaptureJournal(path).summary("revoked-before-drain")
    assert summary["status"] == "failed" and summary["blocks"] == 0
    assert summary["unknown_inflight_loss"] and not summary["loss_count_exact"]
    assert len(pending) == 1 and adapter.state == "fault"
