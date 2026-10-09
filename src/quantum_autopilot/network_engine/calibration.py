"""Full node recalibration as a multi-tick procedure driven by the engine (design §2.4).

Nothing is invented here: the "before" and "after" numbers are what the per-fibre
processing measured (``LinkBank.measurement``), the commissioning numbers are the
author's procedure for the fibre's real length (``sources.commission``), and the
restart is the processing's own (``LinkBank.reset_link`` + the source's
``recommission`` command). With a source that delivers no observables the report says
so: every measured field is ``None`` and the fibre status is ``no_observables``.

Phases (one network second each; names of ``state.CALIBRATION_PHASES``):

====================  =====================================================================
``queued``            accepted; the node leaves the effective graph (demands through it are
                      reallocated at once); the "before" measurement of every incident
                      active fibre is taken.
``polarisation``      controllers, gate and detector baseline of each incident fibre are
                      restarted and the session is recommissioned; the first monitored
                      residual after the initial alignment is recorded.
``sync``              gate reacquisition: the timing residual and gate efficiency after the
                      restart are recorded.
``commissioning``     decoy-BB84 commissioning for the fibre's length (signal / decoy
                      intensity, clock, calibration rate) is recorded next to the
                      transmitter settings the session is really running.
``memory``            the node's repeater memory is cleared (persisted with the report).
``report``            verification: the "after" measurement (at least four settling periods
                      after the restart), the per-fibre verdict, the report is persisted
                      through ``SimulatedTopologyStore.record_node_calibration`` with the
                      ORIGINAL request.
``complete``          the node is back in the effective graph; demands are reallocated again.
====================  =====================================================================

Version conflicts. The store increments the topology version when the report is
recorded and refuses the record if the topology changed after the request. The
procedure therefore **holds topology changes while it runs** (``hold_reason``; the
engine and the service refuse them with that message) and runs one node at a time.
If the topology is changed anyway (another process, the store used directly), the
record is refused: the procedure ends ``failed`` with that reason, nothing is
persisted, the node returns to the graph and the fibres keep the restarted sessions.
The request is never re-based: its hash is what command reconciliation compares.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..control_plane.inventory import RecalibrateNode
from .sources import commission
from .state import CALIBRATION_PHASES, CalibrationLive, Decision, NetEvent, canonical_json, canonical_sha256

CONTROLLER_ID = "procedure.full_recalibration"
#: Ticks a fibre stays ``calibrating`` after its restart before the "after" measurement is read.
SETTLE_TICKS = 4
QBER_LIMIT = 0.11
#: Budget kept below the store's 16 KiB report limit.
REPORT_BUDGET_BYTES = 15 * 1024
_STEPS = ("polarisation", "sync", "commissioning", "memory", "report")


def _measurement(bank, link_id: str):
    try:
        return bank.measurement(link_id)
    except KeyError:
        return None


def _value(measurement, name: str):
    return None if measurement is None else getattr(measurement, name)


@dataclass
class _Run:
    request: RecalibrateNode
    calibration_id: str
    started_tick: int
    started_at_utc: str
    links: dict                           # link_id -> distance_km of the incident active fibres
    records: dict = field(default_factory=dict)
    phase: str = "queued"
    steps_done: int = 0
    reset_tick: int | None = None


@dataclass(frozen=True)
class CalibrationOutcome:
    decisions: tuple = ()
    events: tuple = ()
    topology_changed: bool = False
    finished: CalibrationLive | None = None
    node_id: str | None = None
    error: str | None = None


class CalibrationManager:
    """At most one running recalibration; remembers the last result of every node."""

    def __init__(self, store, bank, *, settle_ticks: int = SETTLE_TICKS):
        if type(settle_ticks) is not int or settle_ticks < 1:
            raise ValueError("settle_ticks must be a positive integer")
        self.store, self.bank, self.settle_ticks = store, bank, settle_ticks
        self._run: _Run | None = None
        self._last: dict = {}             # node_id -> CalibrationLive (complete | failed)
        self.last_error: dict = {}        # node_id -> reason of the last failure

    # -- queries ----------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._run is not None

    def nodes(self) -> frozenset:
        """Nodes that are out of the effective graph because they are being recalibrated."""
        return frozenset() if self._run is None else frozenset({self._run.request.node_id})

    def hold_reason(self) -> str | None:
        """Why a topology change is refused right now, or ``None``."""
        if self._run is None:
            return None
        remaining = max(len(_STEPS) - self._run.steps_done, 1)
        return (f"node {self._run.request.node_id} is being recalibrated (phase {self._run.phase}); topology "
                f"changes are held until it completes (about {remaining} network seconds), because the "
                "calibration report is recorded against the topology version of its request")

    def live(self, node) -> CalibrationLive | None:
        """The calibration shown for a node: the running one, else the last result of this
        engine, else the report persisted in the topology (``console_control.calibration``)."""
        node_id = node.node_id
        if self._run is not None and self._run.request.node_id == node_id:
            run = self._run
            return CalibrationLive(run.calibration_id, "running", run.phase,
                                   CALIBRATION_PHASES.index(run.phase) / (len(CALIBRATION_PHASES) - 1),
                                   run.started_tick, None, None, self._public_links(run), None, None)
        if node_id in self._last:
            return self._last[node_id]
        control = node.telemetry.get("console_control", {})
        stored = control.get("calibration") if isinstance(control, Mapping) else None
        if not isinstance(stored, Mapping) or not isinstance(stored.get("calibration_id"), str):
            return None
        duration = stored.get("duration_s")
        return CalibrationLive(stored["calibration_id"], "complete", "complete", 1.0,
                               stored.get("started_at_tick") if type(stored.get("started_at_tick")) is int else None,
                               stored.get("completed_at_utc"),
                               float(duration) if isinstance(duration, (int, float)) and duration >= 0 else None,
                               dict(stored.get("links") or {}), stored.get("memory_reset"),
                               stored.get("report_sha256"))

    @staticmethod
    def _public_links(run: _Run) -> dict:
        return {link_id: dict(record) for link_id, record in sorted(run.records.items())}

    # -- start ------------------------------------------------------------------------

    def start(self, request: RecalibrateNode, topology, *, tick: int, at_utc: str) -> CalibrationOutcome:
        """Accept a recalibration. Raises ``ValueError`` when it cannot run now."""
        if not isinstance(request, RecalibrateNode):
            raise ValueError("validated RecalibrateNode request required")
        if self._run is not None:
            raise ValueError(f"node {self._run.request.node_id} is being recalibrated; one recalibration runs at "
                             "a time (each report is recorded against its own topology version)")
        if topology.topology_id != request.topology_id:
            raise ValueError("recalibration request addresses another topology")
        if topology.topology_version != request.expected_version:
            raise ValueError(f"topology version conflict: expected {request.expected_version}, found "
                             f"{topology.topology_version}; reload before editing")
        node = topology.node(request.node_id)                     # raises for an unknown node
        links = {link.link_id: float(link.distance_km) for link in topology.links
                 if link.active and link.technology is None and node.node_id in (link.left_node_id, link.right_node_id)}
        calibration_id = "cal-" + canonical_sha256({"node_id": node.node_id, "tick": int(tick), "at_utc": at_utc,
                                                    "expected_version": request.expected_version})[:16]
        run = _Run(request, calibration_id, int(tick), at_utc, links)
        for link_id, distance in links.items():
            before = _measurement(self.bank, link_id)
            run.records[link_id] = {
                "distance_km": distance,
                "qber_before": _value(before, "qber"), "key_rate_hz_before": _value(before, "key_rate_hz"),
                "sync_residual_ps_before": _value(before, "sync_residual_ps"),
                "polarisation_residual_mrad_before": _value(before, "polarisation_residual_mrad"),
                "measured_before_at_tick": _value(before, "tick"),
                "qber_after": None, "key_rate_hz_after": None, "sync_residual_ps_after": None,
                "polarisation_residual_mrad_after": None, "gate_efficiency_after": None,
                "signal_intensity": None, "decoy_intensity": None, "clock_rate_hz": None,
                "commissioned_rate_hz": None, "settling_periods": None, "status": "running"}
        self._run = run
        self._last.pop(node.node_id, None)
        self.last_error.pop(node.node_id, None)
        text = (f"full recalibration of {node.node_id} accepted: node leaves the effective graph; "
                f"{len(links)} incident active fibre(s) " + (", ".join(sorted(links)) if links else "(memory "
                                                              "reset only)"))
        return CalibrationOutcome(
            decisions=(self._decision(run, tick, at_utc, text, "queued"),),
            events=(NetEvent.create(tick=int(tick), at_utc=at_utc, kind="calibration_started", subject=node.node_id,
                                    severity="info", message=text),),
            node_id=node.node_id)

    def _decision(self, run: _Run, tick: int, at_utc: str, summary: str, outcome: str) -> Decision:
        return Decision.create(tick=int(tick), at_utc=at_utc, kind="calibration", controller_id=CONTROLLER_ID,
                               model_state="active", subject=run.request.node_id, summary=summary,
                               inputs_sha256=canonical_sha256({"calibration_id": run.calibration_id,
                                                               "links": self._public_links(run)}),
                               outcome=outcome, in_contract=True, fallback_used=False)

    # -- per tick -----------------------------------------------------------------------

    def before_frame(self, source, *, tick: int) -> None:
        """Start of a tick: the restart of the incident fibres happens before their input is read."""
        run = self._run
        if run is None or run.phase != "queued" or tick <= run.started_tick:
            return
        commands = {}
        for link_id in run.links:
            try:
                commands[link_id] = self.bank.reset_link(link_id)
            except KeyError:
                continue
        if commands:
            source.actuate(commands)
        run.reset_tick = int(tick)

    def after_bank(self, *, tick: int, at_utc: str) -> CalibrationOutcome:
        """End of a tick's fibre processing: advance one phase; record the report at the end."""
        run = self._run
        if run is None or tick <= run.started_tick:
            return CalibrationOutcome()
        phase = _STEPS[min(run.steps_done, len(_STEPS) - 1)]
        if phase == "report" and tick - (run.reset_tick or tick) < self.settle_ticks:
            run.phase = phase
            return CalibrationOutcome()
        run.phase, run.steps_done = phase, run.steps_done + 1
        measurements = {link_id: _measurement(self.bank, link_id) for link_id in run.links}
        fresh = {link_id: item for link_id, item in measurements.items()
                 if item is not None and run.reset_tick is not None and item.tick >= run.reset_tick}
        if phase == "polarisation":
            for link_id, item in fresh.items():
                run.records[link_id]["polarisation_residual_mrad_after"] = item.polarisation_residual_mrad
            text = "polarisation realignment: controllers restarted, " + self._numbers(
                run, fresh, "polarisation_residual_mrad", "{:.0f} mrad monitored residual")
        elif phase == "sync":
            for link_id, item in fresh.items():
                run.records[link_id]["sync_residual_ps_after"] = item.sync_residual_ps
                run.records[link_id]["gate_efficiency_after"] = item.gate_efficiency
            text = "sync reacquisition: " + self._numbers(run, fresh, "sync_residual_ps", "{:.1f} ps residual")
        elif phase == "commissioning":
            for link_id, distance in run.links.items():
                record, item = run.records[link_id], fresh.get(link_id)
                record["commissioned_rate_hz"] = commission(distance).calibration_rate_hz if distance > 0 else None
                # The settings the session is really running after the restart; None without observables.
                record["signal_intensity"] = _value(item, "signal_intensity")
                record["decoy_intensity"] = _value(item, "decoy_intensity")
                record["clock_rate_hz"] = _value(item, "clock_rate_hz")
            text = "decoy-BB84 commissioning for the fibre length: " + self._numbers(
                run, fresh, "signal_intensity", "mu {:.3f}")
        elif phase == "memory":
            text = "repeater memory reset (stored pairs discarded; persisted with the report)"
        else:
            return self._finish(run, fresh, tick, at_utc)
        return CalibrationOutcome(decisions=(self._decision(run, tick, at_utc, f"{run.request.node_id}: {text}",
                                                            phase),), node_id=run.request.node_id)

    @staticmethod
    def _numbers(run: _Run, fresh: Mapping[str, Any], name: str, pattern: str) -> str:
        if not run.links:
            return "no incident active fibre"
        parts = []
        for link_id in sorted(run.links):
            value = _value(fresh.get(link_id), name)
            parts.append(f"{link_id} " + ("no observables" if value is None else pattern.format(value)))
        return "; ".join(parts)

    def _report(self, run: _Run, tick: int, at_utc: str, *, compact: bool) -> dict:
        links = {}
        for link_id, record in sorted(run.records.items()):
            entry = dict(record)
            if compact:
                for name in ("polarisation_residual_mrad_before", "polarisation_residual_mrad_after",
                             "gate_efficiency_after", "key_rate_hz_before", "commissioned_rate_hz",
                             "measured_before_at_tick", "settling_periods", "distance_km", "clock_rate_hz"):
                    entry.pop(name, None)
            links[link_id] = entry
        observed = [record for record in run.records.values() if record["status"] != "no_observables"]
        return {"calibration_id": run.calibration_id, "node_id": run.request.node_id,
                "started_at_tick": run.started_tick, "started_at_utc": run.started_at_utc,
                "completed_at_utc": at_utc, "completed_at_tick": int(tick),
                "duration_s": float(tick - run.started_tick), "phases": list(CALIBRATION_PHASES[1:-1]),
                "links": links, "memory_reset": True,
                "observables": "available" if observed else "unavailable",
                "note": ("measurements are those of the per-fibre processing; fields are null where the input "
                         "source delivered no observables"),
                "physical_action_performed": False}

    def _finish(self, run: _Run, fresh: Mapping[str, Any], tick: int, at_utc: str) -> CalibrationOutcome:
        node_id = run.request.node_id
        for link_id in run.links:
            record, item = run.records[link_id], fresh.get(link_id)
            if item is None:
                record["status"] = "no_observables"
                continue
            record.update(qber_after=item.qber, key_rate_hz_after=item.key_rate_hz,
                          sync_residual_ps_after=item.sync_residual_ps,
                          polarisation_residual_mrad_after=item.polarisation_residual_mrad,
                          gate_efficiency_after=item.gate_efficiency, signal_intensity=item.signal_intensity,
                          decoy_intensity=item.decoy_intensity, clock_rate_hz=item.clock_rate_hz,
                          settling_periods=int(item.period) + 1)
            healthy = item.qber is not None and item.qber < QBER_LIMIT and (item.key_rate_hz or 0.0) > 0.0
            record["status"] = "ok" if healthy else "degraded"
        report = self._report(run, tick, at_utc, compact=False)
        if len(canonical_json(report).encode("utf-8")) > REPORT_BUDGET_BYTES:
            report = self._report(run, tick, at_utc, compact=True)
        self._run = None
        try:
            topology = self.store.record_node_calibration(run.request, report)
        except ValueError as exc:
            reason = (f"recalibration of {node_id} could not be recorded: {exc}. The topology changed while the "
                      "procedure ran (it was requested against version "
                      f"{run.request.expected_version}); nothing was persisted. The fibres keep their restarted "
                      "sessions; request the recalibration again."
                      if "version conflict" in str(exc) else
                      f"recalibration of {node_id} could not be recorded: {exc}; nothing was persisted")
            live = CalibrationLive(run.calibration_id, "failed", "report", None, run.started_tick, at_utc,
                                   float(tick - run.started_tick), self._public_links(run), None, None)
            self._last[node_id], self.last_error[node_id] = live, reason
            return CalibrationOutcome(
                decisions=(self._decision(run, tick, at_utc, reason, "failed"),),
                events=(NetEvent.create(tick=int(tick), at_utc=at_utc, kind="calibration_failed", subject=node_id,
                                        severity="warning", message=reason),),
                finished=live, node_id=node_id, error=reason)
        stored = topology.node(node_id).telemetry["console_control"]["calibration"]
        live = CalibrationLive(run.calibration_id, "complete", "complete", 1.0, run.started_tick, at_utc,
                               float(tick - run.started_tick), dict(stored.get("links") or {}), True,
                               stored["report_sha256"])
        self._last[node_id] = live
        verdicts = ", ".join(f"{link_id} {record['status']}" for link_id, record in sorted(run.records.items())) \
            or "no incident active fibre: memory reset only"
        text = (f"full recalibration of {node_id} complete in {tick - run.started_tick} network seconds; "
                f"{verdicts}; report {stored['report_sha256'][:12]} recorded (topology version "
                f"{topology.topology_version}); node back in the effective graph")
        return CalibrationOutcome(
            decisions=(self._decision(run, tick, at_utc, text, "complete"),),
            events=(NetEvent.create(tick=int(tick), at_utc=at_utc, kind="calibration_completed", subject=node_id,
                                    severity="info", message=text),),
            topology_changed=True, finished=live, node_id=node_id)


__all__ = ["CONTROLLER_ID", "CalibrationManager", "CalibrationOutcome", "SETTLE_TICKS"]
