"""Record the ordinary authenticated Qt demo; no synthesized UI or ready metrics."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.name != "nt" or not args.ffmpeg.is_file():
        parser.error("native Windows Qt and an existing ffmpeg encoder required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frames = output / "frames"
    frames.mkdir()
    from PySide6.QtCore import QBuffer, QIODevice, QPointF, QTimer, Qt
    from PySide6.QtTest import QTest
    from quantum_autopilot.console.app import build_parser, run
    from quantum_autopilot.console.performance_validation import _choose_combo_by_keyboard
    from run_trl4_console_campaign import uses_stage_viewport
    report = {"schema": "orion.native-metro-recording.v1", "preset": "salvador_metro",
              "scope": "QScreen.grabWindow: actual native application window, QTest UI actions",
              "authentication_bypassed": False, "physical_action_performed": False,
              "fps": 5, "actions": [], "received_ticks": [], "errors": []}

    class Driver:
        def attach(self, app, window):
            self.app, self.window = app, window
            app.setQuitOnLastWindowClosed(False)
            window.resize(1600, 1000)
            self.began = time.monotonic()
            self.recorded_at = None
            self.last_tick = None
            self.index = 0
            self.last_jpeg = None
            self.stage = "login"
            self.changed_at = self.began
            self.login_requested = False
            self.authenticated = False
            self.finishing = False
            window._login_controller.succeeded.connect(self.verified)
            window.provider.snapshot_changed.connect(self.received)
            self.timer = QTimer(window)
            self.timer.setInterval(200)
            self.timer.timeout.connect(self.poll)
            self.timer.start()

        def verified(self, principal):
            self.authenticated = principal.source == "os"
            report["identity_source"] = principal.source
            report["subject_sha256"] = hashlib.sha256(principal.subject.encode()).hexdigest()

        def received(self, snap):
            if snap.live is not None and snap.live.tick != self.last_tick:
                self.last_tick = snap.live.tick
                report["received_ticks"].append({"at_s": time.monotonic()-self.began,
                    "tick": snap.live.tick, "source": snap.live.source_kind,
                    "engine_ms": snap.presentation_stats.get("last_network_step_wall_ms")})

        def click(self, widget, point=None):
            if not widget.isVisible() or not widget.isEnabled():
                raise RuntimeError("UI control unavailable: " + widget.objectName())
            point = point or widget.rect().center()
            stage = self.window.centralWidget()
            if uses_stage_viewport(widget, stage):
                from PySide6.QtCore import QPointF
                point = stage.mapFromScene(stage.proxy.mapToScene(QPointF(widget.mapTo(stage.content, point))))
                QTest.mouseClick(stage.viewport(), Qt.MouseButton.LeftButton, pos=point)
            else:
                QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point)

        def action(self, name):
            report["actions"].append({"action": name, "wall_s": time.monotonic()-self.began,
                                      "tick": self.last_tick})
            self.changed_at = time.monotonic()

        def capture(self):
            if self.recorded_at is None:
                return
            now = time.monotonic()-self.recorded_at
            pixmap = self.window.screen().grabWindow(int(self.window.winId()))
            if pixmap.isNull():
                raise RuntimeError("native window capture is empty")
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not pixmap.save(buffer, "JPG", 85):
                raise RuntimeError("native capture encoding failed")
            current = bytes(buffer.data())
            target = min(899, int(now*5))
            # Repeat the preceding real frame for missed capture intervals;
            # preserve wall-clock freezes instead of accelerating the video.
            while self.index < target:
                (frames / f"frame-{self.index:06}.jpg").write_bytes(self.last_jpeg or current)
                self.index += 1
            (frames / f"frame-{self.index:06}.jpg").write_bytes(current)
            self.index += 1
            self.last_jpeg = current

        def finish(self):
            if not self.finishing:
                self.capture()
                self.finishing = True
                self.window.provider.stop()
            if not self.window.provider._collect_retiring_sources():
                report["audit_valid"] = self.window._security.audit.verify()["valid"]
                self.timer.stop()
                self.app.quit()

        def poll(self):
            try:
                if self.finishing:
                    self.finish()
                    return
                if time.monotonic()-self.began > 170:
                    raise TimeoutError("bounded recording deadline")
                self.capture()
                now = time.monotonic()
                w = self.window
                if self.stage == "login":
                    if w.current_screen == "boot" and w.boot.os_session_button.isEnabled() and not self.login_requested:
                        assert not w._skip_auth and not w.boot._fixture_entry
                        self.login_requested = True
                        self.click(w.boot.os_session_button)
                        self.action("sign_in_os_identity")
                    if self.authenticated and w.current_screen == "topology" and len(report["received_ticks"]) >= 4:
                        self.recorded_at = now
                        self.action("ordinary_backend_running")
                        self.stage = "focus"
                elif self.stage == "focus" and now-self.changed_at >= 3:
                    topo = w.screens["topology"]
                    geo = topo.geo
                    _choose_combo_by_keyboard(geo.focus_node, "R-07", self.click)
                    self.action("focus_R-07_from_map_selector")
                    self.stage = "select"
                elif self.stage == "select" and now-self.changed_at >= 2:
                    geo = w.screens["topology"].geo
                    renderer = geo.renderer
                    point = renderer.node_position("R-07").toPoint()
                    assert renderer.hit(QPointF(point)) == ("node", "R-07")
                    self.click(renderer, point)
                    self.action("select_repeater_from_map")
                    self.stage = "isolate"
                elif self.stage == "isolate" and now-self.changed_at >= 3:
                    opened = w.screens["topology"].node_windows.windows.get("R-07")
                    if opened is not None and opened.presentation_status()["ready"]:
                        self.click(opened.isolate)
                        self.action("isolate_repeater_ui")
                        self.stage = "restore"
                elif self.stage == "restore" and now-self.changed_at >= 4:
                    topo = w.screens["topology"]
                    if topo._net.node("R-07").isolated and topo.node_windows.windows["R-07"].isolate.isEnabled():
                        report["isolation_readback"] = True
                        self.click(topo.node_windows.windows["R-07"].isolate)
                        self.action("restore_repeater_ui")
                        self.stage = "close_detail"
                elif self.stage == "close_detail" and now-self.changed_at >= 4:
                    topo = w.screens["topology"]
                    if not topo._net.node("R-07").isolated and topo.node_windows.windows["R-07"].close_button.isEnabled():
                        report["restoration_readback"] = True
                        self.click(topo.node_windows.windows["R-07"].close_button)
                        self.click(topo.inspector.tabs["pairs"])
                        self.action("inspect_pair_routes")
                        self.stage = "keys"
                elif self.stage == "keys" and now-self.changed_at >= 4:
                    self.click(w.rail.buttons["keys"])
                    self.action("inspect_key_management")
                    self.stage = "audit"
                elif self.stage == "audit" and now-self.changed_at >= 4:
                    self.click(w.rail.buttons["audit"])
                    self.action("inspect_backend_audit")
                    self.stage = "logout"
                elif self.stage == "logout" and now-self.changed_at >= 4:
                    self.click(w.rail.sign_out)
                    self.action("sign_out_ui")
                    self.stage = "end"
                elif self.stage == "end" and now-self.changed_at >= 3:
                    report["returned_to_login"] = w.current_screen == "boot"
                    self.finish()
            except Exception as error:
                report["errors"].append({"stage": self.stage, "type": type(error).__name__, "message": str(error)})
                self.finish()

    driver = Driver()
    parsed = build_parser().parse_args(["--provider", "demo", "--demo-preset", "salvador_metro",
                                       "--repository-root", str(ROOT), "--state-root", str(output/"state")])
    result = run(parsed, startup_monitor=driver)
    report["qt_exit"] = result
    report["frame_count"] = driver.index
    report["status"] = "PASS" if not report["errors"] and all(report.get(k) is True for k in
        ("isolation_readback", "restoration_readback", "returned_to_login", "audit_valid")) else "FAIL"
    if driver.index:
        video = output/"orionq-metro-demo.mp4"
        encoded = subprocess.run([str(args.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "5", "-i", str(frames/"frame-%06d.jpg"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "22", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(video)], capture_output=True, text=True, timeout=90)
        report["encoder_returncode"] = encoded.returncode
        if encoded.returncode == 0:
            report["video_sha256"] = hashlib.sha256(video.read_bytes()).hexdigest()
        else:
            report["status"] = "FAIL"
            report["encoder_error"] = encoded.stderr[-1500:]
    (output/"report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("received_ticks", "subject_sha256")}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
