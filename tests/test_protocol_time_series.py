"""Real receipt timestamps, technology-specific observables and explicit gaps."""
from copy import deepcopy

from quantum_autopilot.network_engine.protocol_history import ProtocolHistory
from quantum_autopilot.console.protocol_series import protocol_series_from_state


def publication(*, clock=10., received=9.75, origin="simulated", source="fixture-A", status="operational"):
    return {"clock_s": clock, "source_id": source, "origin": origin, "sessions": {"TF-GOOGLE": {
        "config": {"technologies": ["tf_qkd"], "path": ["US-GOOG-QA", "CENTER", "PEER"], "enabled": True},
        "source": {"origin": origin}, "status": status, "reason": "waiting for accepted acquisition" if received is None else "",
        "metrics": {"last_observed_at_s": received, "qber": .03125, "phase_reference_residual_rad": -.0045,
                    "conditional_key_estimate_bps": 1375., "sifted_bits": 250, "certified_secret_bits": 0,
                    "fidelity": .98, "stability": .94, "wave": [.9, .95]}, "counters": {"observations": 1}}}}


def metric(series, name):
    return next(item for item in series if item["metric"] == name)


def test_tf_history_uses_real_receipt_times_and_never_derives_bell_or_wave():
    history = ProtocolHistory()
    state = publication()
    original = deepcopy(state)
    series = history.update(state, at_utc="2026-10-08T00:00:00Z")
    assert state == original
    assert metric(series, "qber")["samples"] == [{"at_s": 9.75, "value": .03125, "reason": "",
                                                   "origin": "simulated", "source_id": "fixture-A"}]
    assert metric(series, "phase_reference_residual_rad")["unit"] == "rad"
    assert metric(series, "conditional_key_estimate_bps")["unit"] == "bit/s"
    assert metric(series, "conditional_key_estimate_bps")["bounds"] == [0., None]
    assert not {"fidelity", "stability", "wave"}.intersection(item["metric"] for item in series)
    assert all(item["clock_kind"] == "simulation" and item["time_domain"] == "protocol_clock_s" for item in series)
    assert metric(series, "certified_secret_bits")["samples"][0]["value"] == 0


def test_repeated_publication_does_not_resample_old_observations():
    history = ProtocolHistory()
    history.update(publication())
    series = history.update(publication(clock=11.))
    assert len(metric(series, "qber")["samples"]) == 1
    series = history.update(publication(clock=12., received=11.61))
    assert [point["at_s"] for point in metric(series, "qber")["samples"]] == [9.75, 11.61]


def test_initial_zero_counters_are_missing_science_until_an_accepted_receipt():
    state = publication(received=None, status="configured")
    series = ProtocolHistory().update(state)
    assert all(item["samples"][0]["value"] is None for item in series)
    assert all(item["samples"][0]["reason"] for item in series)


def test_failure_adds_a_null_gap_and_new_receipt_recovers_without_filling_it():
    history = ProtocolHistory()
    history.update(publication())
    history.update(publication(clock=11., status="unavailable"))
    series = history.update(publication(clock=12., received=11.8))
    assert [(point["at_s"], point["value"]) for point in metric(series, "qber")["samples"]] == [
        (9.75, .03125), (11., None), (11.8, .03125)]


def test_new_source_and_clock_reset_drop_old_origin_history():
    history = ProtocolHistory()
    history.update(publication())
    series = history.update(publication(clock=12., received=11.9, origin="measurement_file", source="fixture-B"))
    assert len(metric(series, "qber")["samples"]) == 1
    assert metric(series, "qber")["origin"] == "measurement_file"
    series = history.update(publication(clock=1., received=.9, origin="measurement_file", source="fixture-B"))
    assert metric(series, "qber")["samples"][0]["at_s"] == .9


def test_count_and_time_retention_are_bounded_and_removed_sessions_disappear():
    history = ProtocolHistory(max_samples=3, window_s=2.)
    for index in range(1, 10):
        series = history.update(publication(clock=float(index), received=index - .2))
    assert len(metric(series, "qber")["samples"]) <= 3
    assert all(point["at_s"] >= 7. for point in metric(series, "qber")["samples"])
    assert history.update({"clock_s": 10., "sessions": {}}) == []


def test_segment_fidelity_requires_heralds_and_has_local_scope():
    state = publication()
    session = state["sessions"]["TF-GOOGLE"]
    session["config"]["technologies"] = ["entanglement_swapping"]
    session["metrics"]["segments"] = {"US-GOOG-QA|CENTER": {"at_s": 9.6, "heralds": 0,
                                                            "fidelity_estimate": .999, "origin": "simulated"}}
    series = ProtocolHistory().update(state)
    segment = metric(series, "segment_fidelity:US-GOOG-QA|CENTER")
    assert segment["scope"] == "segment" and segment["node_ids"] == ["US-GOOG-QA", "CENTER"]
    assert segment["samples"][0]["value"] is None


def test_frontend_projects_points_and_gaps_without_mutating_or_adding_samples():
    state = publication()
    state["protocol_series"] = ProtocolHistory().update(state)
    original = deepcopy(state)
    projected = protocol_series_from_state(state, node_id="US-GOOG-QA")
    qber = next(item for item in projected if item.metric == "qber")
    assert qber.points == ((9.75, .03125),)
    assert qber.origin == "simulated" and qber.unit == "1"
    assert protocol_series_from_state(state, node_id="CIMATEC") == ()
    assert state == original
