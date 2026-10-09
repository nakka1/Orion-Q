"""Diagnostic boundary tests; the actual Qt workload is measured separately."""
from pathlib import Path

import pytest

from quantum_autopilot.desktop import main
from quantum_autopilot.console import performance_validation as performance


@pytest.mark.parametrize("extra", [
    ["--ui", "web"], ["--demo"], ["--synthetic", "load"],
    ["--state-root", "operator-state"], ["--self-test", "report.json"],
    ["--wdm-model-bundle", "model.npz"], ["--qnet-archive", "author.zip"],
])
def test_performance_cli_rejects_operator_state_and_other_inputs(tmp_path, extra):
    output = tmp_path / "diagnostic"
    with pytest.raises(SystemExit) as exc:
        main(["--performance-test", str(output), *extra])
    assert exc.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("extra", [
    ["--performance-duration", "nan"], ["--performance-soak", "-1"],
    ["--performance-size", "bad"], ["--performance-size", "20x20"],
])
def test_performance_cli_validates_limits_before_creating_state(tmp_path, extra):
    output = tmp_path / "diagnostic"
    with pytest.raises(SystemExit) as exc:
        main(["--performance-test", str(output), *extra])
    assert exc.value.code == 2
    assert not output.exists()


def test_performance_cli_does_not_replace_an_existing_workspace(tmp_path, monkeypatch):
    output = tmp_path / "operator-state"
    output.mkdir()
    original = output / "journal.sqlite"
    original.write_bytes(b"preserve operator journal")
    monkeypatch.setattr(performance, "_run_ui", lambda args: pytest.fail("must not construct Qt"))
    with pytest.raises(SystemExit) as exc:
        main(["--performance-test", str(output)])
    assert exc.value.code == 2
    assert original.read_bytes() == b"preserve operator journal"


def test_performance_cli_dispatches_the_shared_workload_with_fresh_state(tmp_path, monkeypatch):
    output = tmp_path / "diagnostic"
    seen = []
    def record(args):
        seen.append(args)
        assert not (args.output / "state").exists()
        assert (args.output / "configuration.json").is_file()
        return 0
    monkeypatch.setattr(performance, "_run_ui", record)
    assert main(["--performance-test", str(output), "--performance-duration", "20",
                 "--performance-soak", "3", "--performance-size", "3440x1440"]) == 0
    assert len(seen) == 1
    assert seen[0].width == 3440 and seen[0].height == 1440
    assert seen[0].soak == 3 and seen[0].duration == 20
    assert seen[0].source_hashes_start


def test_profile_flag_requires_explicit_diagnostic_output():
    with pytest.raises(SystemExit) as exc:
        main(["--performance-profile"])
    assert exc.value.code == 2


def test_latency_derivation_preserves_nested_first_paint_and_measures_complete_click():
    actions = [{"elapsed_s": 1., "callback_ms": 200., "next_stage_paint_ms": 60.}]
    paints = [
        {"elapsed_s": 1.05, "duration_ms": 10., "stage_viewport": True},
        {"elapsed_s": 1.3, "duration_ms": 20., "stage_viewport": "True"},
    ]
    performance._derive_after_dispatch_paints(actions, paints)
    assert actions[0]["next_stage_paint_ms"] == 60.
    assert actions[0]["paint_after_dispatch_ms"] == pytest.approx(300.)


def test_fixed_workload_has_the_same_targets_counts_and_order_independent_of_speed():
    arguments = (20, 5, ["USP", "CIMATEC", "BR-CBPF", "IME-USP"],
                 ["SAT-B", "SAT-A"], ["topology", "node", "keys", "telemetry"])
    first = performance._action_plan(*arguments)
    second = performance._action_plan(*arguments)
    assert first == second
    assert first["revision"] == "actual-qt-actions-v3"
    assert all(len(first["phases"][phase]) == 10 for phase in ("map", "globe", "tabs", "swaps"))
    assert len(first["phases"]["soak"]) == 150
    for phase, commands in first["phases"].items():
        assert [item["index"] for item in commands] == list(range(len(commands)))
        assert all(left["due_s"] < right["due_s"] for left, right in zip(commands, commands[1:]))
    assert not first["production_zoom_thresholds_modified"]


def test_modes_and_click_targets_are_separate_and_soak_exercises_over_eighty_swaps():
    plan = performance._action_plan(20, 5, ["CIMATEC"], ["SAT-A"], ["topology"])
    for commands in plan["phases"].values():
        for item in commands:
            if item["action"] == "map-node-hover-click":
                assert item["expected_mode"] == "map" and item["target"] == "CIMATEC"
            elif item["action"] == "satellite-hover-click":
                assert item["expected_mode"] == "globe" and item["target"] == "SAT-A"
    mode_commands = [item for commands in plan["phases"].values() for item in commands
                     if item["action"] in ("button-mode-transition", "scroll-mode-transition")]
    assert len(mode_commands) == 85


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_workload_refuses_silent_reduction_when_a_required_component_is_absent(missing):
    components = [["CIMATEC"], ["SAT-A"], ["topology"]]
    components[missing] = []
    with pytest.raises(ValueError, match="ground endpoints, satellites and pages"):
        performance._action_plan(20, 0, *components)


def test_proxy_selector_uses_visible_popup_events_and_does_not_leave_it_open():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QComboBox, QGraphicsScene, QGraphicsView
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    combo = QComboBox()
    layout.addWidget(combo)
    for ident in ("", "CIMATEC", "USP", "IME-USP"):
        combo.addItem(ident or "All nodes", ident)
    scene = QGraphicsScene()
    scene.addWidget(host)
    stage = QGraphicsView(scene)
    stage.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    stage.resize(400, 220)
    stage.show()
    QTest.qWait(20)
    visible_inputs = []
    def click(widget, position=None):
        assert widget.isVisible()
        visible_inputs.append(widget)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=position or widget.rect().center())
    try:
        for ident in ("CIMATEC", "IME-USP", "USP", "", "CIMATEC"):
            performance._choose_combo_by_keyboard(combo, ident, click)
            assert combo.currentData() == ident
            assert not combo.view().isVisible()
        assert len(visible_inputs) == 5
    finally:
        stage.close()
        app.processEvents()


def test_collector_accounting_deduplicates_shared_records_without_inspecting_values():
    from types import SimpleNamespace
    shared = ["shared", 3.]
    opaque = object()
    monitor = SimpleNamespace(heartbeat_samples=[shared, opaque], paint_samples=[shared])
    shallow = performance._collector_accounting(monitor)
    assert shallow["fields"]["heartbeat_samples"]["rows"] == 2
    assert "unique_reachable_bytes" not in shallow
    deep = performance._collector_accounting(monitor, deep=True)
    assert deep["unique_objects_by_type"]["list"] == 3
    assert deep["unsupported_references_by_type"] == {"object": 1}
    assert deep["not_process_rss"] and deep["cannot_establish_absence_of_leaks"]
    assert deep["fields"]["paint_samples"]["unique_reachable_bytes_in_field_order"] == __import__("sys").getsizeof(monitor.paint_samples)


def test_normal_paint_filter_retains_stage_renderers_and_node_windows_without_qt_queries():
    class Renderer:
        pass
    class ChildRenderer(Renderer):
        pass
    class Window:
        pass
    viewport = object()
    assert performance._normal_paint_target(viewport, viewport, (Renderer, Window))
    assert performance._normal_paint_target(ChildRenderer(), viewport, (Renderer, Window))
    assert performance._normal_paint_target(Window(), viewport, (Renderer, Window))
    assert not performance._normal_paint_target(object(), viewport, (Renderer, Window))


def test_local_paint_observers_preserve_native_virtuals_and_restore_inheritance():
    from types import SimpleNamespace
    records = []
    class Stage:
        def paintEvent(self, event):
            return ("stage", event)
    class Parent:
        def paintEvent(self, event):
            return ("inherited", event)
    class Child(Parent):
        pass
    class Native:
        paintEvent = len  # A native callable has no Python __code__.
    originals = (Stage.paintEvent, Parent.paintEvent, Native.paintEvent)
    monitor = SimpleNamespace(paints_seen=0, paints_retained=0,
        paint=lambda receiver, started, duration, **kw: records.append((type(receiver), kw["stage_override"])))
    restore, handlers = performance._install_paint_event_observers(monitor, Stage, (Child, Native))
    try:
        assert Stage().paintEvent("first") == ("stage", "first")
        assert Child().paintEvent("next") == ("inherited", "next")
        assert Native.paintEvent is originals[2]
        assert records == [(Stage, True), (Child, False)]
        assert monitor.paints_seen == monitor.paints_retained == 2
        assert handlers[-1] == {"class": "Native", "observed": False, "reason": "native handler preserved"}
    finally:
        restore()
    assert Stage.paintEvent is originals[0]
    assert Parent.paintEvent is originals[1]
    assert "paintEvent" not in Child.__dict__
    assert Native.paintEvent is originals[2]


def test_local_paint_observer_retains_exception_and_can_be_restored():
    from types import SimpleNamespace
    records = []
    class Stage:
        def paintEvent(self, event):
            raise RuntimeError("original failure")
    original = Stage.paintEvent
    monitor = SimpleNamespace(paints_seen=0, paints_retained=0,
        paint=lambda *args, **kwargs: records.append(kwargs))
    restore, _ = performance._install_paint_event_observers(monitor, Stage, ())
    try:
        with pytest.raises(RuntimeError, match="original failure"):
            Stage().paintEvent(None)
        assert records == [{"stage_override": True}]
    finally:
        restore()
    assert Stage.paintEvent is original


def test_local_span_observer_records_pre_call_metadata_and_restores_inherited_call():
    import time
    from types import SimpleNamespace
    class Parent:
        def collect(self):
            self.count += 1
            return self.count
    class Child(Parent):
        count = 0
    monitor = SimpleNamespace(created=time.perf_counter(), phase="map", method_samples=[])
    restore = performance._install_local_span_observers(monitor,
        [(Child, "collect", "collector.collect", lambda args, kwargs: {"generation": args[0].count})])
    try:
        assert Child().collect() == 1
        row = monitor.method_samples[0]
        assert row["generation"] == 0
        assert row["method"] == "collector.collect" and row["phase"] == "map"
        assert row["end_elapsed_s"] >= row["elapsed_s"]
        assert row["thread_cpu_end_s"] >= row["thread_cpu_begin_s"]
    finally:
        restore()
    assert "collect" not in Child.__dict__


def test_local_profile_is_separate_from_global_profile_before_output_creation(tmp_path, monkeypatch):
    output = tmp_path / "local-profile"
    seen = []
    monkeypatch.setattr(performance, "_run_ui", lambda args: seen.append(args) or 0)
    assert main(["--performance-test", str(output), "--performance-local-profile"]) == 0
    assert seen[0].local_profile and not seen[0].profile
    with pytest.raises(ValueError, match="must be separate"):
        performance.performance_options(output, profile=True, local_profile=True)
    with pytest.raises(SystemExit) as exc:
        main(["--performance-local-profile"])
    assert exc.value.code == 2


def _materialized_ground_fixture():
    from types import SimpleNamespace as NS
    from quantum_autopilot.console.widgets.node_window import NodeWindow
    node = NS(id="CIMATEC", role="Endpoint", kind="institution", status="up", health="up",
        physical_type="ground_station", isolated=False, located=True, latitude=-12.9, longitude=-38.4, place="Salvador")
    engine = NS(id="link-1", status="ok", distance_km=5., fidelity=.95, qber=.02, skr_bps=100.,
        sync_residual_ps=2., polarisation_residual_mrad=3., stability=.8, wave=(.98, .96), wave_rate_hz=20.,
        detector=NS(alarm=False, label="nominal", state="ready"), provenance={}, controllers=(), status_reasons=())
    link = NS(id="link-1", key="link-1", a="CIMATEC", b="PEER")
    live_node = NS(role=node.role, kind=node.kind, status=node.status, located=node.located, latitude=node.latitude,
        longitude=node.longitude, place=node.place, address=None, port=None, links=(link.key,), memory_state="empty",
        pairs=(), temperature_c=20., calibration_count=0, routes_through=(), calibration=None)
    live = NS(node=lambda ident: live_node if ident == node.id else None, links=(engine,), demands=(),
        technologies={}, tick=1, network_time_s=1., source_kind="simulated")
    snap = NS(live=live, topology=NS(nodes=(NS(id=node.id),)))
    net = NS(node=lambda ident: node if ident == node.id else None, links_of=lambda ident: (link,))
    fields = NodeWindow._field_metadata(node, live_node, snap.topology.nodes[0], (link,))
    identity = {label: fields[key] for label, key in (("Role", "role"), ("Kind", "kind"), ("Status", "status"),
        ("Place", "place"), ("Latitude", "latitude"), ("Longitude", "longitude"), ("Address", "address"), ("Port", "port"))}
    identity["Isolation"] = "not isolated"
    state = {label: fields[key] for label, key in (("Memory state · legacy inventory", "memory_state"),
        ("Stored pairs · legacy inventory", "pairs"), ("Temperature", "temperature_c"), ("Calibrations", "calibration_count"),
        ("Routes through", "routes_through"), ("Fibres", "links"), ("Location", "located"))}
    cards = {link.key: performance._connection_expected(node.id, link, engine)}
    class Materialized:
        node_id = node.id
        def field_texts(self): return dict(fields)
        def legend_labels(self): return ["PEER · link-1 · ok · stability 0.800"]
        def connection_texts(self): return cards
    opened = Materialized()
    opened.title = NS(text=lambda: node.id)
    opened.identity = NS(texts=lambda: identity)
    opened.state = NS(texts=lambda: state)
    opened.calibration_kv = NS(texts=lambda: {"Last calibration": "none recorded"})
    opened.calibration_links = NS(texts=lambda: {})
    opened.routes = NS(texts=lambda: {"Demands": "none"})
    opened.status = NS(text=lambda: "")
    opened._legend = {link.key: object()}
    opened.chart = NS(keys=lambda: [link.key], series=(NS(key=link.key, label="PEER · link-1", samples=engine.wave,
        rate_hz=engine.wave_rate_hz, note=""),), network_time_s=live.network_time_s)
    return opened, snap, net, identity, cards


def test_complete_legacy_readback_checks_real_fields_cards_and_received_chart():
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["complete"] and all(receipt["checks"].values())
    assert receipt["mode"] == "legacy-synchronous-readback"
    assert receipt["presented_tick"] == 1 and receipt["source_kind"] == "simulated"
    assert receipt["widget_content_sha256"] and receipt["widget_field_counts"]["identity"] == 9


@pytest.mark.parametrize("fault", ["identity", "connection", "chart"])
def test_complete_metadata_cannot_mask_incorrect_materialized_fields(fault):
    opened, snap, net, identity, cards = _materialized_ground_fixture()
    if fault == "identity": identity["Role"] = "invented"
    elif fault == "connection": cards["link-1"]["Fidelity"] = "1.0000"
    else: opened.chart.series[0].samples = (.5, .5)
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["checks"]["live_node_metadata"]  # metadata remains valid
    assert not receipt["complete"] and receipt["failed_checks"]


def test_loading_readback_never_forces_widgets_or_snapshot_to_finish():
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    opened.presentation_status = lambda: {"state": "loading", "ready": False, "pending": 1, "pending_items": 10}
    opened.presentation_snapshot = lambda: pytest.fail("must not force completion")
    opened.connection_texts = lambda: pytest.fail("must not inspect unfinished widgets")
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert not receipt["complete"] and receipt["ready"] is False and receipt["state"] == "loading"
    assert receipt["presentation"]["pending_items"] == 10


@pytest.mark.parametrize("fault", [None, "clock", "pending", "snapshot"])
def test_async_completion_requires_exact_snapshot_clock_empty_pending_and_widgets(fault):
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    status = {"state": "ready", "ready": True, "pending": 0, "pending_items": 0,
        "presented_tick": 1, "network_time_s": 1., "source_kind": "simulated",
        "expected_link_keys": ["link-1"], "completed_link_keys": ["link-1"]}
    opened.presentation_status = lambda: status
    opened.presentation_snapshot = lambda: (snap, net)
    if fault == "clock": status["presented_tick"] = 2
    elif fault == "pending": status["pending"] = 1
    elif fault == "snapshot": opened.presentation_snapshot = lambda: None
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["complete"] is (fault is None)
    if fault == "snapshot": assert receipt["state"] == "unknown_snapshot"


def test_unknown_async_ready_is_not_treated_as_synchronous_success():
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    opened.presentation_status = lambda: {"state": "unknown"}
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["ready"] is None and not receipt["complete"]


def test_completion_paint_is_measured_after_full_readback_not_first_loading_paint():
    actions = [{"elapsed_s": 1., "callback_ms": 100., "next_stage_paint_ms": 60.,
        "presentation_verified_elapsed_s": 1.8}]
    paints = [{"elapsed_s": value, "duration_ms": 1., "stage_viewport": True} for value in (1.05, 1.2, 1.7, 1.9)]
    performance._derive_after_dispatch_paints(actions, paints)
    assert actions[0]["next_stage_paint_ms"] == 60.
    assert actions[0]["paint_after_dispatch_ms"] == pytest.approx(200.)
    assert actions[0]["paint_after_presentation_ms"] == pytest.approx(900.)


@pytest.mark.parametrize("altered", [False, True])
def test_satellite_readback_requires_orbital_and_science_tables_not_just_source_state(altered):
    from types import SimpleNamespace as NS
    state = {"device_id": "SAT-A", "origin": "simulated", "orbital_class": "LEO", "model": "Kepler",
        "at_network_s": 42., "at_utc": "2026-10-07T12:00:00Z", "altitude_km": 500., "ecef_km": [1., 2., 3.],
        "velocity_ecef_km_s": [4., 5., 6.], "period_s": 5676., "clock_s": 42., "housekeeping_valid": False}
    orbital = {"Classe / modelo": "LEO · Kepler", "Tempo orbital": "42.000 s", "Época UTC": state["at_utc"],
        "Idade / validade": "— / —", "Altitude": "500.00 km", "Posição ECEF · km": "[1.0, 2.0, 3.0]",
        "Velocidade · km/s": "[4.0, 5.0, 6.0]", "Escopo da posição": "configured ephemeris prediction",
        "Housekeeping válido": "Não", "Período": "5676.0 s", "Fonte orbital": "—"}
    if altered: orbital["Altitude"] = "0.00 km"
    panel = NS(_state=state, stations={}, summary=NS(text=lambda: "Origem: simulated\n"
        "RF/telemetria clássica e carga útil óptica quântica são canais distintos."))
    panel.orbital = NS(texts=lambda: orbital)
    panel.bridge = NS(texts=lambda: {"Conexão": "Nenhuma sessão satelital cadastrada"})
    panel.isl = NS(texts=lambda: {"ISL": "Nenhum enlace satélite–satélite cadastrado; proximidade não implica comunicação"})
    panel.operations = NS(texts=lambda: {"Demandas": "Nenhuma demanda atribuída a este satélite no snapshot"})
    opened = NS(node_id="SAT-A", title=NS(text=lambda: "SAT-A"), satellite_panel=panel)
    net = NS(node=lambda ident: NS(physical_type="satellite"), satellites={"SAT-A": state}, communicators={})
    snap = NS(live=NS(tick=42, network_time_s=42., source_kind="simulated"))
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["complete"] is (not altered)
    assert receipt["checks"]["satellite_snapshot_values"]
    assert receipt["checks"]["orbital_widget_values"] is (not altered)


def test_window_components_use_only_progress_scalars_and_keep_unknown_versions_null():
    from types import SimpleNamespace as NS
    def forbid(): pytest.fail("component sampling must not read field corpus")
    loading = NS(presentation_status=lambda: {"ready": False, "state": "loading", "pending": 1,
        "pending_items": 7, "step_max_ms": 12.}, content_texts=forbid, presentation_snapshot=forbid)
    ready = NS(presentation_status=lambda: {"ready": True, "state": "ready", "pending": 0,
        "pending_items": 0, "step_max_ms": 20.}, content_texts=forbid)
    error = NS(presentation_status=lambda: {"ready": False, "state": "error", "pending": 0,
        "pending_items": 1, "step_max_ms": 3.}, content_texts=forbid)
    counters = performance._presentation_component_counts({"loading": loading, "ready": ready, "error": error})
    assert counters["node_windows_total"] == counters["node_windows_status_supported"] == 3
    assert counters["node_windows_pending"] == counters["node_windows_ready"] == counters["node_windows_error"] == 1
    assert counters["node_windows_max_pending_items"] == 7 and counters["node_windows_max_step_ms"] == 20.
    unknown = performance._presentation_component_counts({"legacy": NS(), "ready": ready})
    assert unknown["node_windows_status_unknown"] == 1 and unknown["node_windows_status_supported"] == 1
    assert unknown["node_windows_ready"] is None and unknown["node_windows_max_pending_items"] is None
    empty = performance._presentation_component_counts({})
    assert empty["node_windows_ready"] == empty["node_windows_pending"] == empty["node_windows_error"] == 0


def test_global_counters_read_builtin_lengths_without_calling_properties_or_copying_sets():
    from types import SimpleNamespace as NS
    seen, claimed, outbox = {1, 2}, {"a"}, ["record"]
    manager = NS(_seen_receipt_sequences=seen, _claimed_quantum_receipts=claimed, _outbox=outbox)
    active = NS(service=NS(_network_engine=NS(global_operations=manager)))
    counters = performance._cached_global_operation_counts(active)
    assert counters["global_seen_receipt_sequences"] == 2 and counters["global_claimed_quantum_receipts"] == 1
    assert counters["global_outbox_rows"] == 1 and counters["global_manager_counters_available"]
    assert manager._seen_receipt_sequences is seen and manager._claimed_quantum_receipts is claimed
    seen.add(3)
    assert performance._cached_global_operation_counts(active)["global_seen_receipt_sequences"] == 3
    class Remote:
        @property
        def service(self): pytest.fail("must not request remote service")
    missing = performance._cached_global_operation_counts(Remote())
    assert missing["global_seen_receipt_sequences"] is None and not missing["global_manager_counters_available"]


def test_logical_geometry_records_actual_widget_geometry_and_dpr():
    from types import SimpleNamespace as NS
    widget = NS(geometry=lambda: NS(x=lambda: 12, y=lambda: 13, width=lambda: 3440, height=lambda: 1392),
                devicePixelRatioF=lambda: 1.25)
    assert performance._logical_geometry(widget) == {
        "x": 12, "y": 13, "width": 3440, "height": 1392, "device_pixel_ratio": 1.25}


@pytest.mark.parametrize("origin", [(0, 0), (-3440, 120), (3440, -1440)])
def test_diagnostic_placement_uses_actual_monitor_origin_without_resizing_or_focus(origin):
    from types import SimpleNamespace as NS
    moved = []
    rect = NS(topLeft=lambda: origin, x=lambda: origin[0], y=lambda: origin[1])
    screen = NS(geometry=lambda: rect, name=lambda: "actual-monitor")
    window = NS(screen=lambda: screen, move=moved.append)
    result = performance._align_diagnostic_window(window)
    assert moved == [origin] and result["requested_screen_top_left"] == list(origin)
    assert result["screen"] == "actual-monitor" and result["applied"]


def test_diagnostic_placement_reports_unknown_screen_without_inventing_position():
    from types import SimpleNamespace as NS
    window = NS(screen=lambda: None, move=lambda value: pytest.fail("must not move without screen"))
    assert performance._align_diagnostic_window(window) == {"applied": False, "reason": "screen unavailable"}


@pytest.mark.parametrize("duration,soak,commands,timeout", [
    (20, 5, 190, 1230), (20, 0, 40, 330), (5, 0, 12, 170),
    (1, 0, 4, 154), (20, .01, 41, 336),
])
def test_external_watchdog_covers_every_fixed_command_without_changing_acceptance(duration, soak, commands, timeout):
    guard = performance._external_watchdog_budget(duration, soak)
    plan = performance._action_plan(duration, soak, ["CIMATEC"], ["SAT-A"], ["topology"])
    assert guard["planned_action_count"] == sum(len(items) for items in plan["phases"].values()) == commands
    assert guard["timeout_s"] == pytest.approx(timeout)
    assert guard["timeout_s"] >= guard["planned_duration_s"] + 150
    assert guard["timeout_s"] >= commands * 6 + 90
    assert guard["internal_readback_deadline_s"] == 5
    assert guard["changes_product_acceptance_deadlines"] is guard["changes_scientific_time"] is False
    assert guard["termination_scope"] == "freshly spawned diagnostic UI PID only"


@pytest.mark.parametrize("fault", [None, "missing", "sections", "rows"])
def test_ready_status_requires_all_sections_and_core_rows_before_reading_widgets(fault):
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    status = {"state": "ready", "ready": True, "pending": 0, "pending_items": 0,
        "presented_tick": 1, "network_time_s": 1., "source_kind": "simulated",
        "expected_link_keys": ["link-1"], "completed_link_keys": ["link-1"],
        "expected_sections": ("identity", "state"), "completed_sections": ("identity", "state"),
        "pending_core_rows": 0}
    opened.presentation_status = lambda: status
    opened.presentation_snapshot = lambda: (snap, net)
    if fault == "missing": del status["completed_sections"]
    elif fault == "sections": status["completed_sections"] = ("identity",)
    elif fault == "rows": status["pending_core_rows"] = 1
    if fault:
        opened.presentation_snapshot = lambda: pytest.fail("must not read snapshot of contradictory ready status")
        opened.connection_texts = lambda: pytest.fail("must not materialize unfinished widgets")
    receipt = performance._node_presentation_evidence(opened, snap, net)
    assert receipt["complete"] is (fault is None)
    assert receipt["section_status_supported"]
    if fault: assert receipt["state"] == "incomplete_status" and receipt["failed_checks"]


def test_section_counters_keep_unsupported_versions_unknown_without_reading_texts():
    from types import SimpleNamespace as NS
    loading = NS(presentation_status=lambda: {"ready": False, "state": "loading", "pending": 1,
        "pending_items": 8, "pending_core_rows": 5, "step_max_ms": 10.,
        "expected_sections": ("identity", "state", "routes"), "completed_sections": ("identity",)})
    rows = performance._presentation_component_counts({"a": loading})
    assert rows["node_windows_max_pending_core_rows"] == 5
    assert rows["node_windows_max_incomplete_sections"] == 2 and rows["node_windows_sections_supported"] == 1
    legacy = NS(presentation_status=lambda: {"ready": True, "state": "ready", "pending": 0})
    rows = performance._presentation_component_counts({"a": loading, "legacy": legacy})
    assert rows["node_windows_sections_supported"] == 1
    assert rows["node_windows_max_pending_core_rows"] is rows["node_windows_max_incomplete_sections"] is None


@pytest.mark.parametrize("elapsed, exceeded", [(4.999, False), (5., False), (5.001, True), (8., True)])
def test_late_correct_content_is_explicit_latency_violation_without_changing_collection_exit(elapsed, exceeded):
    effect = {"content_complete_and_corresponds": True, **performance._details_deadline_evidence(10., 10. + elapsed)}
    action = {"action": "map-node-hover-click", "phase": "map", "requested_id": "CIMATEC", "functional_effect": effect}
    summary = performance._details_latency_summary([action])
    assert effect["content_complete_and_corresponds"]  # correctness and timing are separate
    assert effect["readiness_deadline_exceeded"] is exceeded
    assert summary["details_latency_limit_passed"] is (not exceeded)
    assert len(summary["latency_deadline_violations"]) == int(exceeded)
    assert not summary["details_latency_limit_affects_collection_exit"]


def test_absent_legacy_readiness_deadline_is_unknown_not_a_success():
    action = {"functional_effect": {"content_complete_and_corresponds": True}}
    summary = performance._details_latency_summary([action])
    assert summary["details_latency_checks"] == 0 and summary["details_latency_limit_passed"] is None


def test_legacy_reader_uses_received_data_without_new_renderer_metadata_or_content_methods():
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    assert not hasattr(type(opened), "_field_metadata") and not hasattr(opened, "content_texts")
    assert not hasattr(opened, "presentation_status")
    evidence = performance._node_presentation_evidence(opened, snap, net)
    assert evidence["complete"] and evidence["mode"] == "legacy-synchronous-readback"
    assert evidence["checks"]["live_node_metadata"] and evidence["checks"]["identity_widget_values"]
    assert evidence["checks"]["connection_widget_values"] and evidence["checks"]["chart_received_values"]


def test_independent_received_node_formatter_matches_seventeen_known_fields():
    from types import SimpleNamespace as NS
    opened, snap, net, _identity, _cards = _materialized_ground_fixture()
    node = net.node(opened.node_id)
    live = snap.live.node(node.id)
    live.pairs = (.81, .923)
    live.address, live.port = "measurement.local", 8443
    live.routes_through = ("demand-A", "demand-B")
    live.calibration = NS(calibration_id="calibration-1", status="verified", phase="completed")
    fields = performance._received_node_fields(node, live, snap.topology.nodes[0], net.links_of(node.id))
    assert len(fields) == 17
    assert fields["pairs"] == "2 stored · F 0.810, 0.923"
    assert fields["temperature_c"] == "20.00 °C"
    assert fields["calibration"] == "calibration-1 · verified · completed"
    assert fields["routes_through"] == "demand-A, demand-B"
    assert fields["address"] == "measurement.local" and fields["port"] == "8443"
