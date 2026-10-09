"""Exercise a real windowless EXE through the native QProcess adapter.

The outer test harness uses Python; child commands use ONLY the packaged EXE,
from an unrelated cwd and without Python environment/PATH assistance.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.campaign_job import CampaignJob
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.widgets.campaign_runner import CampaignRunner
from quantum_autopilot.console.widgets.campaign_manifest_editor import CampaignManifestEditor
from quantum_autopilot.control_plane.temporal_campaign import TemporalCampaignRequest, TemporalRegime, run_temporal_campaign, read_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from quantum_autopilot.control_plane.attempt_file import verify_attempt_snapshot
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def settle(app, condition, timeout=90):
    until = time.monotonic()+timeout
    while not condition() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    if not condition():
        raise TimeoutError("native worker did not reach the expected state")


def same_summary(left, right):
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_summary(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_summary(a, b) for a, b in zip(left, right))
    if type(left) is float:
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-10)
    return left == right


def validate(exe, output):
    exe, output = Path(exe).resolve(), Path(output).resolve()
    if not exe.is_file():
        raise ValueError("packaged executable not found")
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "executable": str(exe), "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "checks": {}}
    checks = report["checks"]
    root = Path(__file__).resolve().parents[1]
    service = AssuranceDashboardService(repository_root=root, control_journal_path=output / "state with spaces.sqlite")
    app = QApplication.instance() or QApplication([])
    provider = SwitchableProvider(ServiceProvider(service, poll_ms=600000))
    provider.start()
    settle(app, lambda: provider._snap is not None and not provider.busy)
    job = CampaignJob(provider)
    dialog = CampaignRunner()
    dialog.attach(job)
    baseline = TemporalRegime("baseline", 1, .25, .00005, .00003, 8)
    req = TemporalCampaignRequest("frozen-validation", "alice", "repeater-a", "bob", 0, .8, 1e-6, 20,
                                  (7, 8), (baseline, replace(baseline, regime_id="longer-memory", memory_t1_s=.005, memory_t2_s=.003)), "baseline")
    expected = run_temporal_campaign(SimulatedTopologyStore(output / "reference.sqlite"), req)
    old_cwd, old_env, old_executable = Path.cwd(), dict(os.environ), sys.executable
    sentinel = object()
    old_frozen = getattr(sys, "frozen", sentinel)
    old_marker = getattr(sys, "_orion_temporal_worker_v1", sentinel)
    try:
        for name in ("PYTHONPATH", "PYTHONHOME"):
            os.environ.pop(name, None)
        windir = Path(os.environ.get("WINDIR", "C:/Windows"))
        os.environ["PATH"] = str(windir / "System32") + os.pathsep + str(windir)
        cwd = output / "unrelated working directory"
        cwd.mkdir()
        os.chdir(cwd)
        sys.frozen, sys._orion_temporal_worker_v1, sys.executable = True, True, str(exe)
        checks["frozen_capability"] = job.allowed
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        def load(request):
            path = output / f"{request.campaign_id}.json"
            path.write_text(json.dumps(asdict(request)), encoding="utf-8")
            job.load(path)
        load(replace(req, campaign_id="draft-before-apply"))
        editor = CampaignManifestEditor(job, dialog)
        editor.text.setPlainText(json.dumps(asdict(req)))
        editor.apply()
        checks["source_manifest_editor_before_frozen_dispatch"] = (
            job.request == req and not job.active and job.process is None and not dialog.reviewed.isChecked())
        editor.deleteLater()
        saved_manifest = output / "edited-manifest.json"
        job.save_manifest(saved_manifest, expected_request=job.request, expected_context=job._context())
        settle(app, lambda: not job.active)
        job.load(saved_manifest)
        checks["source_manifest_save_reload_before_frozen_dispatch"] = (
            job.request == req and job.process is None and not dialog.reviewed.isChecked())
        dialog.reviewed.setChecked(True)
        dialog.submit()
        settle(app, lambda: not job.active)
        timer.stop()
        checks["responsive_parent"] = len(ticks) >= 3
        if not job.receipt:
            raise RuntimeError(job.note)
        checks["confirmed_receipt"] = job.note.startswith("CONFIRMED")
        checks["source_numeric_parity"] = same_summary(expected["record"]["result"]["summaries"], job.receipt["record"]["result"]["summaries"])
        trials = job.receipt["record"]["result"]["trials_by_regime"]["baseline"]
        checks["aged_density_matrix_exercised"] = any(
            r["result"]["quantum_execution"] and any(age > 0 for age in r["result"]["memory_ages_s"]) for r in trials)
        digest = job.receipt["record_sha256"]
        job.export(output / "confirmed-receipt.json")
        settle(app, lambda: not job.active)
        checks["exported_complete_receipt"] = json.loads((output / "confirmed-receipt.json").read_text(encoding="utf-8"))["record_sha256"] == digest
        job.start()
        settle(app, lambda: not job.active)
        checks["idempotent_replay"] = job.receipt["record_sha256"] == digest and "IDEMPOTENT REPLAY" in job.note
        load(replace(req, campaign_id="cancel-at-start"))
        job.start()
        job.cancel()
        settle(app, lambda: not job.active)
        checks["cancel_before_commit"] = job.note.startswith("CANCELLED") and read_temporal_campaign(service.topology_store, "cancel-at-start") is None
        load(replace(req, campaign_id="cancel-after-progress", seeds=tuple(range(64))))
        def cancel_after_progress():
            if job.active and job.completed > 0 and not job.cancel_requested:
                job.cancel()
        job.changed.connect(cancel_after_progress)
        job.start()
        settle(app, lambda: not job.active)
        job.changed.disconnect(cancel_after_progress)
        checks["cancel_after_progress"] = job.note.startswith("CANCELLED") and job.completed > 0 and read_temporal_campaign(service.topology_store, "cancel-after-progress") is None
        checks["no_workspace_mutation"] = service.topology_store.get(req.topology_id) is None
        checks["no_live_child"] = job.process is None
        journal_before_reads = service.topology_store.path.read_bytes()
        job.browse_history()
        settle(app, lambda: not job.active)
        index = next(i for i, entry in enumerate(job.history_entries) if entry.campaign_id == req.campaign_id)
        job.open_history(index)
        settle(app, lambda: not job.active)
        checks["history_verified_without_worker"] = (job.receipt is not None and job.receipt["record_sha256"] == digest
                                                     and job.process is None and job.request is None and "HISTORICAL" in job.note)
        job.browse_history(campaign_prefix=req.campaign_id)
        settle(app, lambda: not job.active)
        checks["source_history_search_on_frozen_receipt"] = (bool(job.history_entries)
            and all(entry.campaign_id.startswith(req.campaign_id) for entry in job.history_entries)
            and job.history_prefix == req.campaign_id and job.process is None)
        job.browse_attempts()
        settle(app, lambda: not job.active)
        statuses = {entry["record"]["status"] for entry in job.attempts}
        checks["source_operational_attempts_for_frozen_worker"] = (bool(job.attempts) and "OPEN" not in statuses
            and "CANCELLED" in statuses and bool(statuses & {"CONFIRMED", "REPLAYED", "RECOVERED"}))
        selected_attempt = job.attempts[0]
        attempt_export = output / "operational-attempt.json"
        job.export_attempt(0, attempt_export)
        settle(app, lambda: not job.active)
        checks["source_operational_export_for_frozen_worker"] = (
            json.loads(attempt_export.read_text(encoding="utf-8")) == selected_attempt
            and selected_attempt["record"]["schema"] == "orion.campaign_attempt.v1")
        receipt_before = job.receipt
        job.verify_attempt_file(attempt_export)
        settle(app, lambda: not job.active)
        checks["source_external_attempt_verification"] = (
            "SCHEMA + RECORD HASH VERIFIED" in job.attempt_file_text
            and selected_attempt["record_sha256"] in job.attempt_file_text
            and job.receipt is receipt_before and job.process is None)
        job.browse_attempts(campaign_prefix="cancel-", status="CANCELLED")
        settle(app, lambda: not job.active)
        checks["source_filtered_attempts_for_frozen_worker"] = (bool(job.attempts)
            and all(item["record"]["campaign_id"].startswith("cancel-") and item["record"]["status"] == "CANCELLED"
                    for item in job.attempts)
            and job.receipt is receipt_before and job.process is None)
        requested_day = selected_attempt["record"]["requested_at_utc"][:10]
        job.browse_attempts(campaign_prefix="", status="", requested_from=requested_day)
        settle(app, lambda: not job.active)
        checks["source_attempt_period_for_frozen_worker"] = (bool(job.attempts)
            and all(item["record"]["requested_at_utc"][:10] >= requested_day for item in job.attempts)
            and job.receipt is receipt_before and job.process is None)
        job.browse_attempts(requested_from="", requested_before=requested_day)
        settle(app, lambda: not job.active)
        checks["source_attempt_period_excludes_boundary"] = (
            all(item["record"]["requested_at_utc"][:10] < requested_day for item in job.attempts)
            and all(item["record"]["attempt_id"] != selected_attempt["record"]["attempt_id"] for item in job.attempts))
        job.browse_attempts(requested_from="", requested_before="")
        settle(app, lambda: not job.active)
        expected_page = job.attempts
        page_path = output / "operational-page.zip"
        job.export_attempt_page(page_path)
        settle(app, lambda: not job.active)
        with ZipFile(page_path) as bundle:
            wrapper = json.loads(bundle.read("manifest.json"))
            manifest = wrapper["manifest"]
            snapshots = tuple(verify_attempt_snapshot(json.loads(bundle.read(member["file"]))) for member in manifest["members"])
            checks["source_operational_page_export"] = (bundle.testzip() is None and bool(expected_page)
                and snapshots == expected_page and manifest["page_only"] is True
                and manifest["count"] == len(expected_page) and wrapper["manifest_sha256"] == _sha256(manifest)
                and job.receipt is receipt_before and job.process is None)
        job.verify_attempt_archive(page_path)
        settle(app, lambda: not job.active)
        checks["source_operational_page_verification"] = (
            "PAGE ZIP VERIFIED" in job.attempt_file_text and "NOT AUTHENTICATED" in job.attempt_file_text
            and wrapper["manifest_sha256"] in job.attempt_file_text
            and job.receipt is receipt_before and job.process is None)
        linked_index = next(i for i, item in enumerate(job.attempts)
                            if item['record']['receipt_sha256'] == digest)
        linked_attempt = job.attempts[linked_index]
        job.open_attempt_receipt(linked_index)
        settle(app, lambda: not job.active)
        checks["source_attempt_receipt_association"] = (
            job.receipt is not None and job.receipt['record_sha256'] == digest
            and 'ASSOCIATION VERIFIED' in job.note
            and linked_attempt['record']['attempt_id'] in job.provenance_text
            and job.request is None and job.process is None)
        checks["source_history_reads_preserve_journal_bytes"] = service.topology_store.path.read_bytes() == journal_before_reads
        report["passed"] = all(checks.values())
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if job.active:
            job.cancel()
            settle(app, lambda: not job.active, timeout=30)
        job.shutdown()
        provider.stop()
        dialog.close()
        sys.executable = old_executable
        for name, value in (("frozen", old_frozen), ("_orion_temporal_worker_v1", old_marker)):
            if value is sentinel:
                delattr(sys, name)
            else:
                setattr(sys, name, value)
        os.environ.clear()
        os.environ.update(old_env)
        os.chdir(old_cwd)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = validate(args.exe, args.output_dir)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
