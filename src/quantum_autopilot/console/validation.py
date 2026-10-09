"""Self-test that uses only the resources inside the application distribution."""
from __future__ import annotations

import json
import os
from pathlib import Path


def validate_demo_pipeline(paths, destination: Path) -> dict:
    """The demonstration and the idle operator service, read through the same provider class.

    Demonstration = `demo_service`: the ordinary service and network engine on their own
    journal, fed by the backend's simulated INPUT source. The checks compare what the screens
    show with what the engine published — nothing is asserted from a fixture — and confirm that
    the idle service fabricates no observable. In the frozen build this also proves that the
    network engine, the author's vendored packages and the Salvador base map are bundled.
    """

    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, demo_service
    from .geodata import load_salvador
    from .screens.telemetry import TelemetryScreen, backend_link_thresholds
    from .screens.topology import TopologyScreen
    from .service_provider import ServiceProvider
    from .synthetic import SyntheticProvider
    from .providers import MockProvider

    app = QApplication.instance()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    checks: dict = {}
    basemap = load_salvador()
    checks["salvador_basemap_bundled"] = bool(basemap.land) and bool(basemap.roads) and bool(basemap.attribution)

    service = demo_service(paths.repository_root, destination / "state")
    provider = ServiceProvider(service, state_root=destination / "state" / "demo")
    checks["demo_is_the_service_provider"] = (type(provider) is ServiceProvider and provider.is_demo
                                              and not isinstance(provider, (SyntheticProvider, MockProvider)))
    for _ in range(4):
        service.network_step()                       # four network seconds of the real pipeline
    state = service.network_live_state()
    snap = provider.build()
    live = snap.live
    checks["demo_input_is_simulated_only"] = (snap.input_kind == "demo" and live is not None
                                              and live.source_kind == "simulated" == state["source"]["kind"])
    in_graph = [link for link in state["links"] if link["status"] in ("up", "degraded")]
    checks["demo_processing_produced_observables"] = bool(in_graph) and all(
        link["qber"] is not None and link["fidelity"] is not None and len(link["wave"]) > 0 for link in in_graph)
    checks["snapshot_carries_the_engine_values"] = live is not None and all(
        live.link(link["link_id"]).qber == link["qber"] and live.link(link["link_id"]).fidelity == link["fidelity"]
        and list(live.link(link["link_id"]).wave) == list(link["wave"]) for link in state["links"])
    stages = ["input_validation", "quantum_send", "quantum_receive", "classical_send",
              "classical_receive", "observation_validation"]
    checks["observables_require_both_configured_ports"] = bool(in_graph) and all(
        link["provenance"]["channels"]["status"] == "received"
        and link["provenance"]["channels"]["stages"] == stages
        and link["provenance"]["channels"]["physical_transmission"] is False
        and link["provenance"]["channels"]["configuration"]["classical"]["channel_id"] !=
            link["provenance"]["channels"]["configuration"]["quantum"]["channel_id"]
        for link in in_graph)
    checks["demands_planned_by_the_backend"] = bool(state["demands"]) and all(
        demand["status"] in ("routed", "rerouted", "blocked", "no_route") for demand in state["demands"])
    decisions = service.network_decisions(50)
    checks["decisions_journalled_with_hashes"] = bool(decisions) and all(
        row["record"]["record_sha256"] == row["record_sha256"] for row in decisions)
    checks["thresholds_published_by_the_engine"] = (
        live is not None and live.threshold("qber_down") == service.network_engine.bank.policy.qber_down)

    topology = TopologyScreen()
    topology.apply(snap)
    topology.show()
    app.processEvents()
    located = sorted(node["node_id"] for node in state["nodes"] if node["located"])
    waiting = sorted(node["node_id"] for node in state["nodes"] if not node["located"])
    checks["geo_is_the_default_view"] = topology.view == "GEO" and topology.geo.available
    checks["geo_shows_the_located_nodes"] = sorted(topology.geo.placed_ids()) == located and bool(located)
    checks["queue_shows_the_unlocated_nodes"] = sorted(topology.queue.node_ids()) == waiting
    checks["matrix_lists_every_node_and_fibre"] = (
        topology.matrix.node_ids() == [node["node_id"] for node in state["nodes"]]
        and topology.matrix.link_keys() == {link["link_id"] for link in state["links"]})
    hub = max(state["nodes"], key=lambda node: (node["located"], len(node["links"])))
    topology._node_clicked(hub["node_id"])
    app.processEvents()
    window = topology.node_windows.windows.get(hub["node_id"])
    from .responsive_validation import wait_for_node_presentation
    presentation = wait_for_node_presentation(window, lambda: topology._snap, lambda: topology._net)
    checks["node_window_wave_is_backend_samples"] = window is not None and bool(window.chart.keys()) and all(
        value in live.link(key).wave for key in window.chart.keys()
        for _at, value in window.chart.visible_samples(key))
    checks["node_window_details_readable_and_scrollable"] = window is not None and (
        window.legend_host.y() >= window.chart.geometry().bottom() + 1
        and all(row.height() >= row.minimumSizeHint().height() for row in (
            *window._legend.values(), *window.identity.rows.values(), *window.state.rows.values()))
        and window.scroll.verticalScrollBar().maximum() > 0)
    topology.grab().save(str(destination / "demo-topology.png"))
    topology.shutdown()
    topology.close()
    topology.deleteLater()

    telemetry = TelemetryScreen()
    telemetry.apply(snap)
    telemetry._set_metric("qber")
    checks["telemetry_threshold_from_live_state"] = (
        telemetry.chart.threshold == state["policy"]["qber_down"] == backend_link_thresholds(live)["qber"])
    telemetry.close()
    telemetry.deleteLater()

    idle_service = AssuranceDashboardService(repository_root=paths.repository_root,
                                             control_journal_path=destination / "state" / "idle.sqlite3")
    idle_service.network_step()
    idle = ServiceProvider(idle_service, state_root=destination / "state").build()
    checks["idle_service_same_engine_class"] = type(idle_service.network_engine) is type(service.network_engine)
    checks["idle_service_fabricates_no_observable"] = idle.live is not None and idle.live.source_kind == "none" \
        and all(link.qber is None and link.fidelity is None and link.skr_bps is None and not link.wave
                for link in idle.live.links) and all(link.fidelity == "—" for link in idle.topology.links)
    checks["no_physical_action"] = all(row["record"].get("physical_action_performed", False) is False
                                       for row in decisions)
    app.processEvents()
    return {"passed": all(checks.values()), "checks": checks, "ticks": state["tick"],
            "source": state["source"], "physical_action_performed": False,
            "node_presentation": presentation,
            "scope": "real service pipeline on simulated input; no fixture provider involved"}


def run_self_test(paths, report_path: Path) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": {}, "repository_root": str(paths.repository_root)}
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
        from . import theme
        from .screens import SCREENS, BootScreen
        from .service_provider import ServiceProvider

        from .display import configure_high_dpi
        configure_high_dpi()
        app = QApplication.instance() or QApplication([])
        report["qt_platform"] = app.platformName()
        # Offscreen Qt may not enumerate Windows fonts. Read installed fonts
        # for visual validation only; they are not redistributed.
        font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "consola.ttf", "seguisl.ttf"):
            if (font_root / name).is_file():
                QFontDatabase.addApplicationFont(str(font_root / name))
        theme.install(app)
        service = AssuranceDashboardService(
            repository_root=paths.repository_root,
            control_journal_path=paths.control_journal,
            wdm_model_bundle_path=paths.wdm_model_bundle)
        provider = ServiceProvider(service, state_root=paths.state_root, replay_limit=3)
        snap = provider.build()
        checks = report["checks"]
        integrity = service.summary()["integrity"]
        checks["evidence_valid"] = integrity["valid"] and integrity["grid_source_artifact_verified"]
        checks["model_loaded"] = service.wdm_model_status()["valid"]
        checks["qkd_example_available"] = all(
            kv.value != "—" for kv in snap.keys.finite_key if kv.key in {"QBER REPORTED", "SKR", "DECISION"})
        checks["physical_executor_unavailable"] = not service.control_plane_status()["physical_executor_available"]
        for cls in (BootScreen, *SCREENS):
            screen = cls()
            screen.apply(snap)
            screen.show()
            app.processEvents()
            checks[f"frame_{screen.key}"] = (screen.width(), screen.height()) == (theme.DESIGN_W, theme.DESIGN_H)
            if screen.key == "telemetry":
                checks["metric_labels"] = [v.text() for v in screen.card_titles] == [v.label for v in snap.telemetry.headline]
                checks["metric_values"] = [v[0].text() for v in screen.card_hosts] == [v.value for v in snap.telemetry.headline]
            if screen.key == "topology":
                ids = [n.id for n in snap.topology.nodes]
                checks["graph_ids"] = [node_id for _, node_id in screen.graph._boxes] == ids
                checks["matrix_ids"] = screen.matrix.node_ids() == ids
                # every node is either on the map or in the queue under it, never both, never missing
                checks["geo_and_queue_ids"] = sorted(screen.geo.placed_ids() + screen.queue.node_ids()) == sorted(ids)
            screen.grab().save(str(report_path.parent / f"console-{screen.key}.png"))
            screen.close()
            screen.deleteLater()
        from .shell import ConsoleWindow
        from .responsive_validation import validate_background_polling, validate_link_editor_window
        window = ConsoleWindow(provider, skip_auth=True)
        from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
        mark_isolated_diagnostic_window(window)
        window._on_snapshot(snap)
        window.show()
        link_editor_checks = {}
        try:
            from .window_chrome_validation import validate_window_chrome
            report['window_chrome'] = validate_window_chrome(window, report_path.parent / 'window-chrome')
            checks['frameless_window_controls'] = report['window_chrome']['passed']
            from .terminal_validation import validate_terminal
            report['terminal_workspace'] = validate_terminal(window, report_path.parent / 'terminal-workspace')
            checks['terminal_resize_close_reopen'] = report['terminal_workspace']['passed']
            for width, height in ((1440, 900), (1920, 1080), (3440, 1440)):
                window.resize(width, height)
                link_editor_checks[f"{width}x{height}"] = validate_link_editor_window(
                    window, report_path.parent / "link-editor-window" / f"{width}x{height}")
            polling_pages = {page: validate_background_polling(window,
                report_path.parent / "background-polling" / page, page=page) for page in ("topology", "fpga")}
            report["background_polling"] = {"passed": all(result["passed"] for result in polling_pages.values()),
                "pages": polling_pages, "qt_platform": app.platformName(), "checks": {
                    f"{page}_{name}": passed for page, result in polling_pages.items() for name, passed in result["checks"].items()}}
            checks["background_polling_keeps_controls_steady"] = report["background_polling"]["passed"]
        finally:
            window.screens["topology"].link_editor.close()
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        report["link_editor_window"] = link_editor_checks
        checks["link_editor_preserves_main_window"] = all(result["passed"] for result in link_editor_checks.values())
        provider.run_scenario()
        checks["scenario_persisted"] = service.control_plane_topology()["persisted"]
        provider.select_node("repeater-a")
        checks["node_selection"] = provider.snapshot().node.id == "repeater-a"
        provider.export_record("self-test")
        checks["exported"] = bool(list(paths.state_root.glob("console_export_*.json")))
        checks["audit_persisted"] = any(
            e["kind"] == "console_exported"
            for e in service.controller_runtime.journal.list_recent_safety_events())
        from .responsive_validation import validate_responsive_ui
        report["responsive_ui"] = validate_responsive_ui(paths, report_path.parent / "responsive")
        checks["responsive_ui_matrix"] = report["responsive_ui"]["passed"]
        from .topology_validation import validate_functional_topology
        report['functional_topology'] = validate_functional_topology(paths, report_path.parent / 'functional-topology')
        checks['functional_nodes_and_links'] = report['functional_topology']['passed']
        report["demonstration"] = validate_demo_pipeline(paths, report_path.parent / "demonstration")
        checks["demonstration_real_pipeline"] = report["demonstration"]["passed"]
        from .institutional_validation import validate_institutional_demo
        report["institutional_demonstration"] = validate_institutional_demo(report_path.parent / "institutional-demonstration", paths.repository_root)
        checks["institutional_map_and_confirmed_ml_control"] = report["institutional_demonstration"]["passed"]
        from ..quantum_protocols.validation import validate_modular_runtime
        report['modular_quantum_technologies'] = validate_modular_runtime(report_path.parent / 'modular-quantum')
        checks['five_modular_quantum_technologies'] = report['modular_quantum_technologies']['passed']
        from ..network_engine.global_validation import validate_global_network
        report['global_network_operations']=validate_global_network(report_path.parent/'global-network',paths.repository_root,steps=6,full=False)
        checks['global_demands_physical_admission_and_causal_receipts']=report['global_network_operations']['passed']
        from .communicators_validation import validate_communicators_geo
        report['communicators_geo'] = validate_communicators_geo(report_path.parent / 'communicators-geo',paths.repository_root)
        checks['communicator_registration_channels_and_orbits'] = report['communicators_geo']['passed']
        from ..quantum_protocols.satellite_validation import validate_satellite_pipeline
        report['satellite_ogs_v2']=validate_satellite_pipeline(report_path.parent/'satellite-ogs-v2',seed=42)
        checks['satellite_ogs_physics_and_read_only_ingress']=report['satellite_ogs_v2']['passed']
        from .satellite_validation import validate_satellite_ui
        report['satellite_ogs_ui']=validate_satellite_ui(report_path.parent/'satellite-ogs-ui',
            report['satellite_ogs_v2'].get('loopback',{}).get('snapshot',{}).get('metrics',{}))
        checks['satellite_housekeeping_science_inspection']=report['satellite_ogs_ui']['passed']
        from ..network_engine.validation import validate_memory_and_distance
        report["network_regressions"] = validate_memory_and_distance(report_path.parent / "network-regressions")
        checks["network_memory_and_distance_regressions"] = report["network_regressions"]["passed"]
        from .usage_validation import validate_usage_feedback
        report['usage_feedback'] = validate_usage_feedback(paths, report_path.parent / 'usage-feedback')
        checks['reading_hover_and_path_results'] = report['usage_feedback']['passed']
        from .text_scroll_validation import validate_text_scrolling
        report['text_scrolling'] = validate_text_scrolling(report_path.parent / 'text-scrolling', repository_root=paths.repository_root)
        checks['all_text_readers_and_editors_scroll'] = report['text_scrolling']['passed']
        from .live_update_validation import validate_live_updates
        report['live_updates'] = validate_live_updates(report_path.parent / 'live-updates', repository_root=paths.repository_root)
        checks['live_tick_delivery_and_numerical_worker'] = report['live_updates']['passed']
        from .fluency_validation import validate_wave_continuity
        report['wave_continuity'] = validate_wave_continuity(report_path.parent / 'wave-continuity')
        checks['rolling_wave_retains_received_left_edge'] = report['wave_continuity']['passed']
        report["passed"] = all(checks.values())
        report["check_count"] = len(checks)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return 0 if report["passed"] else 1
