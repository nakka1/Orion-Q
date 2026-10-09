"""Data providers.

`DataProvider` is the seam. Subclass it, emit `snapshot_changed` whenever new
data lands, and every screen updates itself — no screen ever reaches for a
socket, a file or a device handle.

    class LiveProvider(DataProvider):
        def start(self):
            self._sub = daq.subscribe("ring04", self._on_frame)
        def _on_frame(self, frame):
            self._snap = build_snapshot(frame)   # -> contract.Snapshot
            self.snapshot_changed.emit(self._snap)

`MockProvider` below is a full UI fixture plus a 1 Hz jitter for widget tests and
layout review. It is not the demonstration and not a data source: see its docstring.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from .capabilities import SourceCapabilities
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from .contract import (
    Alert, Artefact, Boot, Chart, ClaimCheck, Event, KV, KeySession, Keys, Ledger,
    LedgerEntry, Link, LogLine, Meter, Metric, Node, NodeDetail, Role, Series,
    Snapshot, Stage, Telemetry, Topology,
)


class DataProvider(QObject):
    snapshot_changed = Signal(object)   # contract.Snapshot
    export_completed = Signal(str)
    loading_progress = Signal(object)  # measured completed/total/message, never a clock estimate

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snap: Snapshot | None = None

    def start(self) -> None:      # begin streaming
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def snapshot(self) -> Snapshot:
        if self._snap is None:
            raise RuntimeError("provider has no snapshot yet")
        return replace(self._snap, capabilities=self.capabilities)

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities()

    def publish_snapshot(self):
        """Attach a local declaration; input payloads cannot grant capabilities."""
        self._snap = self.snapshot()
        self.snapshot_changed.emit(self._snap)

    # --- commands the UI can issue; a live provider should override these ---
    def recalibrate(self, target: str) -> None:
        print(f"[provider] recalibrate {target}")

    def isolate(self, target: str) -> None:
        print(f"[provider] isolate {target}")

    def acknowledge(self, target: str) -> None:
        print(f"[provider] acknowledge {target}")

    def export_record(self, scope: str) -> None:
        print(f"[provider] export {scope}")


def _clock(offset_s: int = 0) -> str:
    return (datetime(2026, 9, 8, 2, 41, 7) + timedelta(seconds=offset_s)).strftime("%H:%M:%S")


class MockProvider(DataProvider):
    """Deterministic UI fixture with light jitter — a developer/test aid, never a data source.

    Every value below is artificial (the literals of the HTML prototype). It is reachable
    only from tests and from `python -m quantum_autopilot.console.app --provider mock`; the
    login screen cannot select it, the shell labels it "SYNTHETIC · UI FIXTURE" and its
    snapshot says `input_kind="synthetic"`. The demonstration does NOT use this class: it is
    the real service pipeline on simulated input (`ServiceProvider` over `demo_service`).
    """

    is_synthetic = True
    source_label = "SYNTHETIC · UI FIXTURE"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._snap = self._build()

    def start(self) -> None:
        self.snapshot_changed.emit(self._snap)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._t += 1
        self._snap = self._build()
        self.snapshot_changed.emit(self._snap)

    # ------------------------------------------------------------ fixture
    def _build(self) -> Snapshot:
        t = self._t
        fid = 0.9471 + math.sin(t / 9) * 0.0018
        qber = 2.14 + math.sin(t / 7) * 0.06
        skr = 41.2 + math.sin(t / 11) * 0.9
        rng = random.Random(1337)

        nodes = [
            Node("N-01", "LAB HUB · SPDC", "degraded", 0.189, 0.196, "node"),
            Node("N-02", "CAMPUS EAST", "up", 0.500, 0.150),
            Node("N-03", "METRO EDGE", "up", 0.811, 0.250),
            Node("R-07", "REPEATER · 61%", "degraded", 0.257, 0.554, "repeater"),
            Node("CORE", "SWAPPING CORE", "up", 0.541, 0.521, "core"),
            Node("N-05", "PARTNER LAB", "up", 0.838, 0.696),
            Node("N-09", "DARK FIBRE · DOWN", "down", 0.365, 0.832),
            Node("N-11", "GROUND STATION", "up", 0.676, 0.814),
        ]
        links = [
            Link("N-01", "N-02", "N01↔N02", "0.981", "1.4%", "62k", "fibre", 3.0, "green"),
            Link("N-02", "N-03", "N02↔N03", "0.964", "1.9%", "48k", "fibre", 2.0, "green"),
            Link("N-01", "R-07", "N01↔R07", "0.955", "2.2%", "31k", "fibre", 2.0, "amber"),
            Link("R-07", "CORE", "R07↔CORE", "0.902", "4.8%", "11k", "fibre", 4.0, "amber"),
            Link("CORE", "N-03", "CORE↔N03", "0.961", "1.8%", "44k", "fibre", 3.0, "green"),
            Link("CORE", "N-05", "CORE↔N05", "0.958", "2.0%", "39k", "fibre", 2.0, "green"),
            Link("R-07", "N-09", "R07↔N09", "—", "—", "0", "fibre", 1.5, "amber"),
            Link("N-09", "N-11", "N09↔N11", "0.944", "2.8%", "16k", "fibre", 2.0, "green"),
            Link("N-11", "N-05", "N11↔N05", "0.949", "2.6%", "18k", "fibre", 1.5, "green"),
            Link("N-02", "CORE", "N02↔CORE", "—", "—", "0", "fault", 2.0, "red"),
            Link("N-01", "N-05", "entangled pair path", "", "", "", "satellite", 1.0, "violet"),
            Link("N-09", "N-03", "entangled pair path", "", "", "", "satellite", 1.0, "violet"),
        ]

        boot = Boot(
            title="OrionQ",
            title_local="量子ネットワーク統合管制",
            subtitle="INTEGRATED QUANTUM NETWORK CONTROL",
            checks=[
                LogLine(_clock(-7), "Ring 04 · 8 nodes enumerated", "OK", "green"),
                LogLine(_clock(-5), "Repeater R-07 · purification below target", "WARN", "amber"),
                LogLine(_clock(-3), "Key vault · 2.41 Gb available · sealed", "SEALED", "green"),
                LogLine(_clock(-1), "Audit ledger · chain tip verified", "SIGNED", "violet"),
            ],
            fields=[KV("OPERATOR ID", "OP-2291"), KV("HARDWARE TOKEN", "awaiting tap")],
            session=[
                KV("SESSION RECORD", "OP-2291 · TERMINAL 07"),
                KV("LEDGER CHAIN TIP", "3f9a…c214", "violet"),
            ],
            notice="Every command, calibration and key-pool read from this session is written to "
                   "the audit ledger with the operator identity attached.",
            build="BUILD 4.11.0-ORION · TERMINAL 07",
        )

        topology = Topology(
            ring="RING 04 · UI FIXTURE",
            nodes=nodes,
            links=links,
            links_up="9 / 12 UP",
            metrics=[
                Metric("BELL FIDELITY", f"{fid:.3f}", "", "", "measured",
                       "tomography, 10⁴ coincidences/window", "amber"),
                Metric("QBER", f"{qber:.1f}", "%", "", "measured",
                       "sifted, basis-matched errors only", "green"),
                Metric("SECRET KEY RATE", f"{skr:.1f}", "kb/s", "", "derived",
                       "finite-key, ε = 1×10⁻⁹, after PA", "violet"),
            ],
            events=[
                Event("FAULT", "N-09", "02:38:12",
                      "Coincidence rate to CORE at zero. Traffic rerouted through R-07.", "red"),
                Event("WARN", "R-07", "02:31:44",
                      "Purification yield 61%, below the 80% operating target.", "amber"),
                Event("KEY", "N-11", "02:29:03",
                      "Satellite window closed. 184 Mb distilled this pass.", "violet"),
            ],
            footer=[
                KV("DATA SOURCE", "ui fixture · artificial values"),
                KV("CAMPAIGN", "C-2026-037 · night run 12"),
                KV("INSTRUMENTS", "SNSPD-4x · TTM8 sn 2214"),
                KV("SNAPSHOT HASH", "sha256:8be1…04af", "violet"),
            ],
            caveat="Graph geometry is logical, not geographic — distances in the link table are "
                   "fibre lengths, not map distances.",
        )

        def wave(base, amp, period, n=16, phase=0.0):
            return [base + math.sin((i + t * 0.35 + phase) / period) * amp for i in range(n)]

        telemetry = Telemetry(
            headline=[
                Metric("BELL-STATE FIDELITY", f"{fid:.4f}", "", "± 0.0031", "measured",
                       "Overlap of the reconstructed state with |Φ⁺⟩, from full two-qubit "
                       "tomography. Says how good the shared state is — not how much key you get.",
                       "amber"),
                Metric("QBER", f"{qber:.2f}", "%", "± 0.09", "measured",
                       "Error fraction in the sifted, basis-matched sample. A channel-quality "
                       "figure; it bounds the key but is not the key rate.", "green"),
                Metric("SECRET KEY RATE", f"{skr:.1f}", "kb/s", "", "derived",
                       "Output of the finite-key bound (ε = 1×10⁻⁹) after reconciliation and "
                       "privacy amplification. Computed, never measured directly.", "violet"),
            ],
            fidelity=Chart(
                series=[
                    Series("N01↔N02", wave(0.975, 0.012, 2.2), "amber", False, 2.0),
                    Series("CORE↔N05", wave(0.955, 0.010, 2.6, phase=1.1), "green", False, 1.5),
                    Series("R07↔CORE", wave(0.905, 0.016, 3.1, phase=2.3), "red", True, 1.5),
                ],
                y_min=0.86, y_max=1.0,
                x_labels=["01:41", "02:00", "02:15", "02:30", "02:41"],
                threshold=0.900, threshold_label="— — OPERATING THRESHOLD 0.900",
            ),
            coherence=[
                Meter("T₁", 0.78, "12.4 ms", "amber"),
                Meter("T₂*", 0.46, "1.84 ms", "amber"),
                Meter("T₂ echo", 0.63, "4.02 ms", "amber"),
                Meter("η store", 0.71, "71%", "violet"),
            ],
            coherence_note="T₂* from Ramsey (n = 512 shots); echo from Hahn sequence. Both fitted "
                           "with a single exponential — fits with R² < 0.97 are flagged in the "
                           "artefact record.",
            conditions=[
                KV("Cryostat stage", "11.8 mK"),
                KV("Fibre temperature", "19.4 °C ± 0.6"),
                KV("Pump power", "4.2 mW"),
                KV("Detector efficiency", "0.82 / 0.79"),
                KV("Dark counts", "18 s⁻¹"),
                KV("Coincidence window", "500 ps"),
            ],
            claim=ClaimCheck(
                "exceeds", "02:41",
                "Ring 04 sustains Bell fidelity above 0.95 continuously.",
                "Not supported by this dataset: 6 of 240 windows fall below 0.95, and tomography "
                "is sampled every 5 min — the record cannot speak to continuity between samples. "
                "Suggested wording: “median fidelity 0.947 over 60 min, 97.5% of sampled windows "
                "above 0.93”.",
            ),
            limitations=[
                "Detector efficiency assumed constant within each window; no per-window recalibration.",
                "SKR uses the asymptotic collective-attack model with a finite-key correction, not a "
                "device-independent bound.",
                "R07↔CORE data includes 4 min of post-recalibration transient, not excluded.",
            ],
            artefacts=[
                Artefact("tomography_ring04_0241.h5", "sha256:8be1…04af", "41.2 MB"),
                Artefact("coincidence_raw_c2026037.parquet", "sha256:5c07…9d1b", "1.9 GB"),
                Artefact("skr_finitekey_v3.json", "sha256:a412…77e0", "18 KB"),
            ],
            window_note="WINDOW 60 min · TOMOGRAPHY EVERY 5 min · DAQ 250 ms",
        )

        pool: list[str] = (["violet"] * 32) + (["amber"] * 6) + (["dim"] * 10)
        pool = (pool * 11)[:512]
        registers: list[str] = []
        for i in range(128):
            r = rng.random()
            registers.append("dim" if r < 0.19 else "amber" if r < 0.245 else
                             "red" if r < 0.253 else "violet")

        keys = Keys(
            pipeline_link="DISTILLATION PIPELINE · LINK N01↔N02",
            pipeline=[
                Stage("01", "SIFTING", "1.24", "Mb", "basis-matched, 49.8% kept", "ink"),
                Stage("02", "ERROR EST.", "1.4", "% QBER", "sample 12k bits, disclosed", "green"),
                Stage("03", "RECONCILIATION", "1.09", "Mb", "Cascade, 4 passes, f = 1.14", "ink"),
                Stage("04", "PRIVACY AMP.", "824", "kb", "Toeplitz, ε = 1×10⁻⁹", "violet", True),
            ],
            pool_label="KEY POOL · 512 BLOCKS × 4.7 Mb",
            pool_blocks=pool,
            pool_legend=[
                KV("available", "2.41 Gb", "violet"),
                KV("reserved", "0.42 Gb", "amber"),
                KV("consumed", "", "dim"),
            ],
            sessions=[
                KeySession("S-4471", "N01↔N02", "62.4k", "1.4%", "ACTIVE", "b7f2…19ac", "green"),
                KeySession("S-4470", "N02↔N03", "48.1k", "1.9%", "ACTIVE", "30ce…8b41", "green"),
                KeySession("S-4468", "R07↔CORE", "11.0k", "4.8%", "THROTTLED", "a91d…5f60", "amber"),
                KeySession("S-4465", "N11↔N05 (sat)", "18.4k", "2.6%", "CLOSED", "7d04…c2e8", "muted"),
                KeySession("S-4462", "N02↔CORE", "0", "—", "ABORTED", "—", "red"),
            ],
            sessions_note="Fingerprints are truncated SHA-256 of the key commitment, never of key "
                          "material. S-4468 is throttled because its QBER sits above the 3% policy "
                          "ceiling — the pipeline keeps distilling but no key is released.",
            finite_key=[
                KV("Security parameter ε", "1×10⁻⁹"),
                KV("Block size", "10⁶ bits"),
                KV("Reconciliation eff. f", "1.14"),
                KV("Attack model", "collective", "amber"),
            ],
            scope_note="Rates on this screen are secure under a collective-attack assumption with "
                       "trusted detectors. They are not device-independent and must not be "
                       "reported as such.",
            consumers=[
                Meter("tunnel/lab-archive", 0.64, "12 kb/s", "violet"),
                Meter("tunnel/partner-lab", 0.38, "7 kb/s", "violet"),
                Meter("otp/telemetry-seal", 0.16, "3 kb/s", "violet"),
            ],
            provenance=[
                KV("SOURCE", "kms/ring04 · HSM 02"),
                KV("CAMPAIGN", "C-2026-037"),
                KV("DEVICES", "QKD-TX 1102 / QKD-RX 1108"),
                KV("COMMITMENT", "sha256:c93f…7a20", "violet"),
                KV("SEALED", "02:38:44 UTC−3"),
            ],
            status="VAULT SEALED · HSM 02 · POLICY QKD-LAB-3",
        )

        thr: list[tuple[float, str]] = []
        for i in range(23):
            if i in (10, 11):
                thr.append((0.20 + i * 0.002, "red"))
            elif i < 10:
                thr.append((0.62 - i * 0.035 + (0.05 if i % 2 else 0), "amber"))
            else:
                thr.append((0.24 + (i - 12) * 0.018 + (0.04 if i % 2 else 0), "amber"))

        node = NodeDetail(
            id="R-07",
            name_local="量子中継局 · 第三区",
            subtitle="QUANTUM REPEATER · SECTOR 3\nSPIN-PHOTON, 4 MEMORY BANKS",
            alert=Alert(
                "WARNING · PURIFICATION YIELD 61% · SINCE 02:31:44",
                "Entanglement swapping is holding, but yield is below the 80% operating target. "
                "Cause not yet established — do not report the 02:31 drop as a hardware fault "
                "until the calibration rerun completes.",
                "amber", ("RERUN CALIBRATION", "ACKNOWLEDGE"),
            ),
            equipment=[
                KV("MEMORY", "NV-DIAMOND R2 · sn 0417"),
                KV("DETECTORS", "SNSPD-4x · sn 2214"),
                KV("TIME TAGGER", "TTM8 · sn 2214-B"),
                KV("MODULATOR", "LiNbO₃ EOM · sn 7731"),
                KV("FIRMWARE", "rp-4.9.2 · signed"),
                KV("LAST SERVICE", "2026-08-21"),
            ],
            subsystems=[
                KV("Cryogenics", "NOMINAL", "green"),
                KV("Optical alignment", "NOMINAL", "green"),
                KV("Purification", "DEGRADED", "amber"),
                KV("Swapping scheduler", "NOMINAL", "green"),
                KV("Clock sync", "41 ps", "green"),
            ],
            registers=registers,
            register_legend=[
                KV("entangled", "96", "violet"),
                KV("re-priming", "7", "amber"),
                KV("failed", "1", "red"),
                KV("idle", "24", "dim"),
            ],
            throughput=thr,
            throughput_labels=("02:11", "02:31 · yield drop", "02:41"),
            calibration=[
                Meter("Pump power", 0.64, "4.2 mW", "amber", 0.80),
                Meter("Phase bias", 0.47, "0.47 π", "amber", 0.50),
                Meter("Coinc. window", 0.50, "500 ps", "amber", 0.50),
                Meter("Purif. rounds", 0.33, "2 of 3", "red", 0.66),
            ],
            calibration_note="Green ticks mark the values used in campaign C-2026-037. Any change "
                             "from here forward opens a new campaign revision, so data before and "
                             "after are never mixed in one claim.",
            log=[
                LogLine("02:31:44", "Purification yield 61% · below target", "", "amber"),
                LogLine("02:28:19", "Calibration snapshot written · signed", "", "ink"),
                LogLine("02:19:57", "Memory bank 02 re-primed · 7 registers", "", "ink"),
                LogLine("02:04:11", "Clock sync re-locked · 41 ps offset", "", "green"),
            ],
            breadcrumb="TOPOLOGY › RING 04 › R-07",
        )

        ledger = Ledger(
            roles=[
                Role("PRINCIPAL INVESTIGATOR", "2",
                     "Signs campaigns and publishes records. Cannot alter raw artefacts."),
                Role("NETWORK OPERATOR", "5",
                     "Calibrates, reroutes, opens key sessions. Every action is signed.", True),
                Role("KEY CUSTODIAN", "2", "Seals and revokes pools. Two-person rule on revocation."),
                Role("OBSERVER", "14",
                     "Read-only, including provenance and limitations. No export of key data."),
            ],
            sessions=[
                KV("OP-2291 · TERM 07", "OPERATOR", "green"),
                KV("PI-0104 · REMOTE", "PI", "muted"),
                KV("KC-0330 · VAULT RM", "CUSTODIAN", "violet"),
                KV("OB-1177 · CAMPUS", "OBSERVER", "dim"),
            ],
            entries=[
                LedgerEntry("02:41:07", "OP-2291", "Signed in · terminal 07 · quorum 2/3",
                            "3f9a…c214", "system"),
                LedgerEntry("02:38:44", "KC-0330", "Key pool sealed · 512 blocks · HSM 02",
                            "c93f…7a20", "key"),
                LedgerEntry("02:38:12", "system", "Fault recorded · N-09 coincidence zero",
                            "e07b…41c8", "system"),
                LedgerEntry("02:33:50", "OB-1177", "Export denied · role lacks key-data scope",
                            "9b21…0f74", "export", "red"),
                LedgerEntry("02:28:19", "OP-2291", "Calibration snapshot · R-07 · signed",
                            "d41c…88b2", "calibration"),
                LedgerEntry("02:20:03", "PI-0104", "Campaign C-2026-037 revision 4 opened",
                            "1a88…62de", "system"),
                LedgerEntry("02:11:41", "system", "Tomography artefact registered · 41.2 MB",
                            "8be1…04af", "system"),
            ],
            chain_verified="41 892 / 41 892 VERIFIED",
            chain_fraction=1.0,
            chain_note="Each entry hashes the previous one, plus the artefact digests it "
                       "references. A published record therefore names its data, its instruments, "
                       "its conditions and the exact bytes behind every figure.",
            export=[
                KV("SCOPE", "campaign C-2026-037 · rev 4"),
                KV("INCLUDES", "metrics, conditions, limitations"),
                KV("SIGNATURE", "PI-0104 + KC-0330", "violet"),
            ],
            status="LEDGER VERIFIED · 41 892 ENTRIES · TIP 3f9a…c214",
        )

        return Snapshot(
            at=_clock(t),
            operator="OP-2291",
            campaign="C-2026-037",
            counts=[
                KV("● 6 NODES UP", "", "green"),
                KV("▲ 1 DEGRADED", "", "amber"),
                KV("■ 1 DOWN", "", "red"),
            ],
            boot=boot, topology=topology, telemetry=telemetry,
            keys=keys, node=node, ledger=ledger, input_kind="synthetic",
        )
