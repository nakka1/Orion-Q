"""Preparation records, loss accounting, periodic drain and real crash recovery."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from quantum_autopilot.lab.capture import CaptureJournal, SyntheticRateSource, run_continuous_capture
from quantum_autopilot.lab.observations import (ObservationBlock, bind_acquisition_observations,
    ClockReference, load_observation_block, persist_observation_block, synthetic_bb84_observations)
from quantum_autopilot.lab.photon import Acquisition, AcquisitionError, PhotonConfig, TimeTag


def test_identified_observations_reproduce_every_preparation_and_detection(tmp_path):
    block = synthetic_bb84_observations(count=1000, seed=7)
    path = persist_observation_block(block, tmp_path / "observations.json")
    restored = load_observation_block(path)
    assert restored.observation_sha256 == block.observation_sha256
    assert restored.observations == block.observations
    assert sum(p.detected for p in block.observations) == 339
    assert all(p.analyzed_bit is None and p.detection_timestamp_ps is None for p in block.observations if not p.detected)


@pytest.mark.parametrize("mutation", ["basis", "undetected_bit", "duplicate", "regression", "clock", "origin"])
def test_incomplete_or_inconsistent_observation_contract_is_rejected(mutation):
    block = synthetic_bb84_observations(count=10, efficiency=1, loss_db=0)
    pulses = list(block.observations)
    if mutation == "basis":
        pulses[0] = replace(pulses[0], prepared_basis=None)
    elif mutation == "undetected_bit":
        pulses[0] = replace(pulses[0], detected=False)
    elif mutation == "duplicate":
        pulses[1] = pulses[0]
    elif mutation == "regression":
        pulses[1] = replace(pulses[1], timestamp_ps=-1)
    elif mutation == "clock":
        block = replace(block, clock=replace(block.clock, epoch_utc="2026-10-07T00:00:00"))
    elif mutation == "origin":
        block = replace(block, origin="physical")
    with pytest.raises(AcquisitionError):
        replace(block, observations=pulses).validate()


def test_observation_hash_rejects_changed_bits(tmp_path):
    path = persist_observation_block(synthetic_bb84_observations(count=10), tmp_path / "fixture.json")
    payload = json.loads(path.read_text())
    payload["observation"]["observations"][0]["prepared_bit"] ^= 1
    path.write_text(json.dumps(payload))
    with pytest.raises(AcquisitionError, match="integrity"):
        load_observation_block(path)


def test_metadata_join_matches_actual_clicks_and_cannot_invent_bit_from_timestamp():
    cfg = PhotonConfig()
    block = synthetic_bb84_observations(count=10, efficiency=1, loss_db=0)
    events = [TimeTag(p.detection_timestamp_ps, cfg.channels[p.analyzed_bit], block.experiment_id,
                      block.device_id, cfg.configuration_id, cfg.clock_domain, "simulated", "identified-single-photon-fixture")
              for p in block.observations if p.detected]
    raw = Acquisition(block.experiment_id, block.device_id, "fixture", "emulation", "simulated", asdict(cfg),
                      events, block.duration_ps, cfg.clock_domain, "identified-single-photon-fixture")
    joined = bind_acquisition_observations(raw, block.observations, detector_bit_map={1: 0, 2: 1},
                                          preparation_source_id="prepare-log", analysis_source_id="analyzer-log")
    assert joined.raw_acquisition_sha256 and joined.observations == block.observations
    with pytest.raises(AcquisitionError, match="match acquired"):
        bind_acquisition_observations(raw, [replace(block.observations[0], analyzed_bit=1-block.observations[0].analyzed_bit),
                                           *block.observations[1:]], detector_bit_map={1: 0, 2: 1},
                                     preparation_source_id="prepare-log", analysis_source_id="analyzer-log")


@pytest.mark.parametrize("rate,capacity,expected_received,expected_loss", [(20, 100, 200, 0), (200, 30, 300, 1700)])
def test_continuous_capture_conserves_known_production_and_explicitly_records_overflow(tmp_path, rate, capacity, expected_received, expected_loss):
    journal = CaptureJournal(tmp_path / "capture.sqlite")
    source = SyntheticRateSource(events_per_interval=rate, buffer_capacity=capacity)
    summary = run_continuous_capture(source, journal, "stream", drain_interval_s=.001)
    assert summary["status"] == "completed" and summary["blocks"] == 10
    assert summary["produced_events"] == rate * 10
    assert summary["received_events"] == expected_received
    assert summary["reported_lost_events"] == expected_loss
    assert summary["loss_count_exact"] and summary["duplicate_events_written"] == 0
    assert summary["complete"] == (expected_loss == 0)


def test_capture_cancellation_after_durable_block_stops_and_preserves_committed_data(tmp_path):
    cancel = threading.Event()
    source = SyntheticRateSource(intervals=1000)
    journal = CaptureJournal(tmp_path / "capture.sqlite")
    def committed(batch, receipt):
        if batch.source_sequence == 2:
            cancel.set()
    summary = run_continuous_capture(source, journal, "cancel", cancel=cancel, on_block=committed, drain_interval_s=.001)
    assert summary["status"] == "cancelled" and summary["blocks"] == 3
    assert source.stopped and summary["received_events"] == 60


def test_duplicate_drain_is_idempotent_but_different_payload_or_sequence_gap_is_rejected(tmp_path):
    source = SyntheticRateSource()
    source.start()
    block = source.drain()
    journal = CaptureJournal(tmp_path / "capture.sqlite")
    journal.begin("dedup")
    assert not journal.append("dedup", block)["duplicate"]
    assert journal.append("dedup", block)["duplicate"]
    with pytest.raises(AcquisitionError, match="different raw"):
        journal.append("dedup", replace(block, produced_events=None, loss_count_exact=False))
    with pytest.raises(AcquisitionError, match="gap"):
        journal.append("dedup", replace(block, source_sequence=5))
    other = CaptureJournal(journal.path)
    with pytest.raises(AcquisitionError, match="ownership"):
        other.append("dedup", source.drain())
    assert other.recover() == []  # Live producer is never stolen by another process.
    assert journal.finish("dedup")["received_events"] == 20


@pytest.mark.parametrize("uncommitted", [False, True])
def test_real_process_kill_recovers_committed_blocks_without_duplicating_or_claiming_unflushed_buffers(tmp_path, uncommitted):
    database = tmp_path / "crash.sqlite"
    code = """
import sys,time,sqlite3,os
from quantum_autopilot.lab.capture import CaptureJournal,SyntheticRateSource
j=CaptureJournal(sys.argv[1]);j.begin('crash')
s=SyntheticRateSource();s.start();b=s.drain();j.append('crash',b)
if sys.argv[2]=='yes':
    db=sqlite3.connect(sys.argv[1]);db.execute('BEGIN IMMEDIATE')
    db.execute("UPDATE captures SET status='completed' WHERE capture_id='crash'")
print('COMMITTED',os.getpid(),flush=True)
time.sleep(60)
"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(database), "yes" if uncommitted else "no"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
    try:
        ready = child.stdout.readline().strip().split()
        assert ready[0] == "COMMITTED"
        writer_pid = int(ready[1])
        if os.name == "nt":
            # Windows venv executable is a launcher; kill the writer it created.
            completed = subprocess.run(["taskkill", "/PID", str(writer_pid), "/F"], capture_output=True, text=True)
            assert completed.returncode == 0
        else:
            child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    restarted = CaptureJournal(database)
    assert restarted.recover() == ["crash"]
    summary = restarted.summary("crash")
    assert summary["status"] == "interrupted" and summary["blocks"] == 1 and summary["received_events"] == 20
    assert summary["unknown_inflight_loss"] and not summary["loss_count_exact"]
    assert restarted.recover() == []
    with pytest.raises(AcquisitionError, match="already exists"):
        restarted.begin("crash")


def test_capture_hash_detects_tampering(tmp_path):
    journal = CaptureJournal(tmp_path / "tamper.sqlite")
    run_continuous_capture(SyntheticRateSource(intervals=1), journal, "tamper", drain_interval_s=.001)
    import sqlite3
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE capture_blocks SET payload_json='{}'")
    with pytest.raises(AcquisitionError, match="integrity"):
        journal.summary("tamper")


def test_nonterminating_source_times_out_and_retains_explicit_failed_capture(tmp_path):
    from types import SimpleNamespace
    stopped = []
    source = SimpleNamespace(start=lambda: None, drain=lambda: None, stop=lambda: stopped.append(True), finished=False)
    journal = CaptureJournal(tmp_path / "timeout.sqlite")
    with pytest.raises(AcquisitionError, match="timeout"):
        run_continuous_capture(source, journal, "timeout", drain_interval_s=.001, timeout_s=.02)
    assert stopped and journal.summary("timeout")["status"] == "failed"


def test_physical_observation_join_requires_explicit_measured_clock_alignment():
    cfg = PhotonConfig()
    block = synthetic_bb84_observations(count=10, efficiency=1, loss_db=0)
    events = [TimeTag(p.detection_timestamp_ps, cfg.channels[p.analyzed_bit], block.experiment_id,
                      block.device_id, cfg.configuration_id, cfg.clock_domain, "physical", "identified-interface-fixture")
              for p in block.observations if p.detected]
    raw = Acquisition(block.experiment_id, block.device_id, "contract-double", "hardware", "physical", asdict(cfg),
                      events, block.duration_ps, cfg.clock_domain, "identified-interface-fixture")
    kwargs = {"detector_bit_map": {1: 0, 2: 1}, "preparation_source_id": "prepare-log", "analysis_source_id": "analyze-log"}
    with pytest.raises(AcquisitionError, match="measured clock"):
        bind_acquisition_observations(raw, block.observations, **kwargs)
    with pytest.raises(AcquisitionError, match="not aligned"):
        bind_acquisition_observations(raw, block.observations, clock_reference=ClockReference("other-clock"), **kwargs)
    # Contract fixture: this declared alignment is not an experimental validation.
    joined = bind_acquisition_observations(raw, block.observations,
        clock_reference=ClockReference(cfg.clock_domain, alignment_uncertainty_ps=25), **kwargs)
    assert joined.clock.alignment_uncertainty_ps == 25 and joined.origin == "physical"
