"""ORION-Q network engine: live per-fibre processing on real or simulated input.

The only simulation is the generation of INPUT data (``sources.SimulatedInputSource``);
everything downstream is the reviewed processing (``links``), under the model registry
(``models``), and what it produces is published as frozen records (``state``).
No Qt import anywhere in this package.
"""
from .allocation import Allocator
from .calibration import CalibrationManager, CalibrationOutcome
from .engine import VERBS, DecisionJournal, NetworkEngine
from .links import (BankStep, CausalFeatures, LinkBank, LinkMeasurement, LinkPolicy, LinkProcessor, StepControls,
                    werner_fidelity)
from .models import ArchiveState, FastSafeDetector, ModelRegistry
from .presets import default_demands, ensure_demo_workspace, salvador_metro
from .routing import (Demand, DemandStore, RoutePlan, Router, RouterConfig, block_reasons, effective_graph,
                      explain_no_route, imposed_flags)
from .sources import (DEMO_LOOP_S, DEMO_TIMELINE, EVENT_KINDS, ActiveEvent, Commissioning, DomainNote,
                      IdleInputSource, InputEvent, InputFrame, InputSource, LinkCommand, LinkInput,
                      SimulatedInputSource, TimelineEvent, commission, domain_notes)
from .state import (SCHEMA, CalibrationLive, Decision, DemandLive, DetectorLive, LinkHistory, LinkLive, LiveState,
                    ModelStatus, NetEvent, NodeLive, QkdLive, SourceInfo, canonical_json, canonical_sha256)

__all__ = ["SCHEMA", "DEMO_LOOP_S", "DEMO_TIMELINE", "EVENT_KINDS", "VERBS", "ActiveEvent", "Allocator",
           "ArchiveState", "BankStep", "CalibrationLive", "CalibrationManager", "CalibrationOutcome",
           "CausalFeatures", "Commissioning", "Decision", "DecisionJournal", "Demand", "DemandLive", "DemandStore",
           "DetectorLive", "DomainNote", "FastSafeDetector", "IdleInputSource", "InputEvent", "InputFrame",
           "InputSource", "LinkBank", "LinkCommand", "LinkHistory", "LinkInput", "LinkLive", "LinkMeasurement",
           "LinkPolicy", "LinkProcessor", "LiveState", "ModelRegistry", "ModelStatus", "NetEvent", "NetworkEngine",
           "NodeLive", "QkdLive", "RoutePlan", "Router", "RouterConfig", "SimulatedInputSource", "SourceInfo",
           "StepControls", "TimelineEvent", "block_reasons", "canonical_json", "canonical_sha256", "commission",
           "default_demands", "domain_notes", "effective_graph", "ensure_demo_workspace", "explain_no_route",
           "imposed_flags", "salvador_metro", "werner_fidelity"]
