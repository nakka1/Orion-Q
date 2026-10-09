"""Exercise the disconnect sound on native Qt without a service or login.

This diagnostic records Qt playback evidence, not a microphone measurement or
human verification of audibility. A missing audio output is explicitly degraded.
Use --sound-file to exercise the exact WAV shipped with a frozen executable.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
from pathlib import Path
import sys
import time
import wave


def _wave_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as stream:
        samples = stream.readframes(stream.getnframes())
        result = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "channels": stream.getnchannels(), "sample_width_bytes": stream.getsampwidth(),
            "sample_rate_hz": stream.getframerate(), "frames": stream.getnframes(),
            "duration_seconds": stream.getnframes() / stream.getframerate(),
            "compression": stream.getcomptype(),
        }
    if result["sample_width_bytes"] == 2:
        values = array("h", samples)
        if sys.byteorder != "little":
            values.byteswap()
        result["peak_pcm16"] = max((abs(value) for value in values), default=0)
        result["nonzero_samples"] = sum(value != 0 for value in values)
    return result


def run(output: Path, sound_file: Path | None = None, *, timeout_seconds: int = 25) -> int:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
    from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
    from PySide6.QtWidgets import QApplication, QWidget

    from quantum_autopilot.console.widgets import disconnect_signal
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    from quantum_autopilot.product import product_version

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_file = output / "disconnect-audio.json"
    if report_file.exists():
        raise ValueError("Use a new evidence directory")
    report = {
        "schema": "orion.disconnect-audio.v1", "version": product_version(),
        "passed": False, "result": "failed", "playback_verified": False,
        "audibility_verified": False, "service_started": False,
        "authentication_exercised": False, "operator_state_accessed": False,
        "physical_action_performed": False, "frozen": bool(getattr(sys, "frozen", False)),
        "wave_origin": "packaged WAV supplied by --sound-file" if sound_file else "source asset",
        "trace": [], "cycles": [], "checks": {}, "errors": [],
    }
    original_sound = disconnect_signal.DISCONNECT_SOUND
    asset = (sound_file if sound_file else original_sound).resolve()
    began = time.perf_counter()
    deadline = began + timeout_seconds
    phase = "initializing"
    app = None
    host = None
    overlay = None
    heartbeat = None
    heartbeat_gaps = []
    sound_hooks = False
    finished_events = []

    def record(kind: str, **values):
        report["trace"].append({"at_ms": round((time.perf_counter() - began) * 1000, 3),
                                "phase": phase, "event": kind, **values})

    def status_name(status):
        return status.name

    def observe_sound():
        nonlocal sound_hooks
        sound = overlay._sound
        if sound is None:
            return
        if not sound_hooks:
            sound.statusChanged.connect(lambda: record("status_changed", status=status_name(sound.status())))
            sound.playingChanged.connect(lambda: record("playing_changed", playing=sound.isPlaying()))
            sound_hooks = True
        record("sound_snapshot", status=status_name(sound.status()), playing=sound.isPlaying(),
               volume=sound.volume(), loop_count=sound.loopCount())

    def wait_until(predicate, *, limit_seconds=5.):
        local_deadline = min(deadline, time.perf_counter() + limit_seconds)
        while not predicate():
            if time.perf_counter() >= local_deadline:
                raise TimeoutError(f"Diagnostic milestone timed out in phase {phase}")
            app.processEvents()
            time.sleep(.002)

    def wait_milliseconds(milliseconds):
        target = time.perf_counter() + milliseconds / 1000.
        wait_until(lambda: time.perf_counter() >= target, limit_seconds=milliseconds / 1000. + .5)

    def on_finished():
        sound = overlay._sound
        evidence = {"completed": overlay.completed, "is_running": overlay.is_running,
                    "sound_playing": sound.isPlaying() if sound is not None else None,
                    "timer_active": overlay._timer.isActive()}
        finished_events.append({"phase": phase, **evidence})
        record("finished", **evidence)

    def sound_played_in(current_phase):
        return any(item["phase"] == current_phase and item.get("playing") is True
                   for item in report["trace"])

    try:
        report["wave"] = _wave_info(asset)
        app = QApplication.instance() or QApplication([])
        report["platform"] = app.platformName()
        if sys.platform != "win32" or app.platformName() != "windows":
            raise ValueError("Native Windows Qt is required; do not use offscreen for playback evidence")
        devices = QMediaDevices.audioOutputs()
        report["audio_outputs_count"] = len(devices)
        report["audio_outputs"] = [{"description": item.description(), "default": item.isDefault()}
                                   for item in devices]
        if not devices:
            report["result"] = "degraded_no_audio_output"
            report["errors"].append("No audio output is available; playback cannot be verified")
            return 2
        disconnect_signal.DISCONNECT_SOUND = asset
        host = QWidget()
        host.setWindowTitle("OrionQ · isolated disconnect audio diagnostic")
        host.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        host.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        mark_isolated_diagnostic_window(host)
        host.resize(960, 540)
        host.show()
        overlay = disconnect_signal.DisconnectSignalOverlay(host, sound_enabled=True)
        overlay.finished.connect(on_finished)
        last_heartbeat = time.perf_counter()

        def on_heartbeat():
            nonlocal last_heartbeat
            now = time.perf_counter()
            heartbeat_gaps.append((now - last_heartbeat) * 1000.)
            last_heartbeat = now

        heartbeat = QTimer(host)
        heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
        heartbeat.setInterval(25)
        heartbeat.timeout.connect(on_heartbeat)
        heartbeat.start()
        for index in (1, 2):
            phase = f"natural_cycle_{index}"
            cycle_start = time.perf_counter()
            overlay.start()
            observe_sound()
            wait_until(lambda: overlay._sound.status() in (QSoundEffect.Status.Ready, QSoundEffect.Status.Error),
                       limit_seconds=3.)
            observe_sound()
            wait_until(lambda: not overlay.is_running, limit_seconds=5.)
            observe_sound()
            callbacks = [entry for entry in finished_events if entry["phase"] == phase]
            report["cycles"].append({
                "case": phase, "duration_ms": round((time.perf_counter() - cycle_start) * 1000., 3),
                "ready": overlay._sound.status() == QSoundEffect.Status.Ready,
                "playback_started": sound_played_in(phase), "callback_count": len(callbacks),
                "completed": overlay.completed, "sound_stopped": not overlay._sound.isPlaying(),
                "timer_stopped": not overlay._timer.isActive(),
                "input_filter_released": not overlay._input_filter_installed,
                "parent_filter_released": not overlay._parent_filter_installed,
            })
            wait_milliseconds(100)
        phase = "restart_and_cancel"
        overlay.start()
        observe_sound()
        wait_until(lambda: overlay._sound.isPlaying(), limit_seconds=2.)
        wait_milliseconds(400)
        trace_before = len(report["trace"])
        same_sound = overlay._sound
        overlay.start()
        observe_sound()
        wait_until(lambda: overlay._sound.isPlaying(), limit_seconds=2.)
        wait_milliseconds(400)
        restart_trace = report["trace"][trace_before:]
        report["checks"]["restart_reuses_one_sound_instance"] = overlay._sound is same_sound
        report["checks"]["restart_stops_previous_playback"] = any(
            item["event"] == "playing_changed" and item.get("playing") is False for item in restart_trace)
        report["checks"]["restart_starts_playback"] = any(item.get("playing") is True for item in restart_trace)
        overlay.stop()
        wait_milliseconds(100)
        report["checks"].update({
            "cancel_stops_sound": not overlay._sound.isPlaying(),
            "cancel_stops_timer": not overlay._timer.isActive(),
            "cancel_releases_input": not overlay._input_filter_installed and not overlay._parent_filter_installed,
            "cancel_has_no_finished_callback": not any(item["phase"] == phase for item in finished_events),
            "single_playback_loop": overlay._sound.loopCount() == 1,
            "audible_volume_configured": 0 < overlay._sound.volume() <= 1,
            "no_sound_error": not any(item.get("status") == "Error" for item in report["trace"]),
            "non_silent_pcm": report["wave"].get("nonzero_samples", 0) > 0,
            "wave_finishes_before_animation": report["wave"]["duration_seconds"] * 1000
                < disconnect_signal.DISCONNECT_DURATION_MS,
            "two_natural_cycles_completed": len(report["cycles"]) == 2 and all(
                item["ready"] and item["playback_started"] and item["callback_count"] == 1
                and item["completed"] and item["sound_stopped"] and item["timer_stopped"]
                and item["input_filter_released"] and item["parent_filter_released"]
                for item in report["cycles"]),
            "heartbeat_continued": len(heartbeat_gaps) >= 100,
        })
        phase = "dispose"
        overlay.dispose()
        report["checks"]["dispose_stops_sound"] = overlay._sound is None or not overlay._sound.isPlaying()
        report["playback_verified"] = all(report["checks"].values())
        report["passed"] = report["playback_verified"]
        report["result"] = "verified_qt_playback" if report["passed"] else "failed"
        return 0 if report["passed"] else 1
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        if overlay is not None:
            overlay.dispose()
        if host is not None:
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        disconnect_signal.DISCONNECT_SOUND = original_sound
        sorted_gaps = sorted(heartbeat_gaps)
        report["heartbeat"] = {
            "requested_interval_ms": 25, "samples": len(sorted_gaps),
            "max_gap_ms": round(max(sorted_gaps, default=0), 3),
            "p95_gap_ms": round(sorted_gaps[int((len(sorted_gaps) - 1) * .95)], 3) if sorted_gaps else None,
        }
        report["elapsed_seconds"] = round(time.perf_counter() - began, 3)
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_file), "passed": report["passed"], "result": report["result"]}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sound-file", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=25)
    args = parser.parse_args()
    if not 15 <= args.timeout_seconds <= 30:
        parser.error("--timeout-seconds must be between 15 and 30")
    return run(args.output_dir, args.sound_file, timeout_seconds=args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
