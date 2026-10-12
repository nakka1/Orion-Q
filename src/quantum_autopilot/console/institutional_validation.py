"""Bundled-resource checks for the national demonstration and its control switch."""
import json
from pathlib import Path


def validate_institutional_demo(destination, repository_root):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from ..network_engine.control_mode import CONTROL_ROLES
    from ..network_engine.institutional import PRESET_ID, SITES, brazil_institutions
    from ..network_engine.state import Decision
    from ..qkd.dashboard_api import demo_service
    from .geodata import load_brazil, load_world
    from .screens.topology import TopologyScreen
    from .service_provider import ServiceProvider

    out = Path(destination); out.mkdir(parents=True, exist_ok=True)
    service = demo_service(repository_root, out / "state", preset=PRESET_ID, scenario="quiet")
    provider = ServiceProvider(service, state_root=out / "state" / "demo")
    app = QApplication.instance(); checks = {}; screen = None
    try:
        for _ in range(4):
            service.network_step()
        provider.refresh(); state = service.network_live_state()
        legacy_ids={n.node_id for n in brazil_institutions().nodes if n.node_id!='UNIJORGE'}
        legacy_links=[l for l in state['links'] if 'technology' not in l['provenance']]
        checks['unijorge_removed_existing_sites_preserved'] = 'UNIJORGE' not in {n['node_id'] for n in state['nodes']} and legacy_ids <= {n['node_id'] for n in state['nodes']}
        checks['international_modular_topology'] = len(state['nodes'])==76 and len(state['links'])==136 and len(state['technologies']['sessions'])==38
        from ..quantum_protocols.orbital_relay_topology import PATH, SESSION_ID
        checks['hybrid_drivers_registered'] = (
            {'TF-RIO-FINITE-CENTER', *PATH} <= {n['node_id'] for n in state['nodes']}
            and {'RIO-SNS-FINITE', SESSION_ID} <= set(state['technologies']['sessions']))
        operations=state['technologies'].get('global_operations',{})
        institutions=[row for row in operations.get('components',()) if row.get('institutional_endpoint')]
        checks['institutional_send_receive_roles']=len(institutions)==20 and all(
            row['capabilities'].get('originate_receive') for row in institutions)
        checks["twenty_four_hypothetical_repeaters"] = sum(n["node_id"].startswith("R-") for n in state["nodes"] if n["node_id"] in legacy_ids) == 24
        checks["existing_demo_nodes_have_a_usable_span"] = all(n["status"] in ("up", "degraded") for n in state["nodes"] if n["node_id"] in legacy_ids)
        checks["repeater_spans_acquired_on_both_channels"] = all(
            l["provenance"]["channels"]["status"] == "received" and l["skr_bps"] > 0
            for l in state["links"] if l["link_id"].startswith("REP-"))
        checks["ibge_national_map_bundled"] = bool(load_brazil().land) and "IBGE" in load_brazil().attribution
        checks["ml_default_off"] = state["policy"]["ml_control_enabled"] is False
        checks["reference_controls_acting"] = all(m["state"] == "disabled" for m in state["models"] if m["role"] in CONTROL_ROLES)
        checks["long_fibres_not_given_usable_keys"] = all(l["status"] == "down" and l["skr_bps"] == 0 and l["qkd"]["key_pool_bits"] == 0 for l in legacy_links if l["distance_km"] > 300)
        checks["local_fibres_produce_keys"] = all(l["skr_bps"] > 0 for l in legacy_links if l["distance_km"] < 3)
        checks["hypothetical_fibre_provenance"] = all("lower bound" in l["provenance"].get("fibre_geometry", "") for l in legacy_links)
        screen = TopologyScreen(); screen.resize(1180, 820); screen.apply(provider.snapshot()); screen.show(); app.processEvents()
        geo = screen.geo; panel = screen.ml_control
        visible_ids={l['link_id'] for l in state['links']}
        checks["national_view_and_all_connections"] = geo.basemap is load_world() and geo.drawn_link_keys() == visible_ids
        checks["map_controls_do_not_overlap"] = not geo.info.geometry().intersects(geo.controls.geometry())
        for site in SITES:
            ident = site[0]
            if ident=="UNIJORGE":continue
            geo.focus_node.setCurrentIndex(geo.focus_node.findData(ident)); app.processEvents()
            centre = geo.node_position(ident)
            checks[f"focus_and_select_{ident}"] = centre is not None and geo.hit(centre) == ("node", ident) and screen._selection == ("node", ident)
        geo.reset.click(); screen.node_windows.close_all(); app.processEvents()
        screen.grab().save(str(out / "topology.png"))
        technology=screen.technology_panel
        technology.open();app.processEvents()
        checks['protocol_controls_reachable'] = technology.dialog.table.rowCount()==len(state['technologies']['sessions']) and technology.dialog.export.isVisible()
        technology.dialog._capture()
        checks['protocol_evidence_from_backend'] = technology.dialog._data()==state['technologies']
        technology.dialog.grab().save(str(out/'protocols.png'))
        technology.dialog.close()
        sent = []; panel.command.connect(lambda *args: sent.append(args))
        QTimer.singleShot(50, lambda: QTest.mouseClick(panel.dialog.cancel_button, Qt.MouseButton.LeftButton))
        QTest.mouseClick(panel.switch, Qt.MouseButton.LeftButton)
        checks["cancel_has_no_effect"] = not sent and not panel.switch.enabled_mode
        receipts = []
        for enabled in (True, False):
            def confirm():
                checks[f"confirmation_buttons_visible_{enabled}"] = panel.dialog.confirm_button.isVisible() and panel.dialog.cancel_button.isVisible()
                panel.dialog.grab().save(str(out / ("confirm-enable.png" if enabled else "confirm-disable.png")))
                QTest.mouseClick(panel.dialog.confirm_button, Qt.MouseButton.LeftButton)
            QTimer.singleShot(50, confirm)
            QTest.mouseClick(panel.switch, Qt.MouseButton.LeftButton)
            checks[f"confirmation_dispatch_{enabled}"] = bool(sent) and json.loads(sent[-1][1])["enabled"] is enabled
            if not sent:
                continue
            checks[f"no_optimistic_thumb_{enabled}"] = panel.switch.enabled_mode is not enabled
            provider.set_ml_control(sent[-1][1]); screen.apply(provider.snapshot())
            result = provider.last_network_result
            receipts.append(result["receipt"])
            checks[f"backend_readback_{enabled}"] = panel.switch.enabled_mode is enabled and result["enabled"] is enabled
            checks[f"receipt_verifies_{enabled}"] = Decision.from_dict(result["receipt"]).verify()
        checks["detector_still_active"] = next(m for m in service.network_live_state()["models"] if m["role"] == "detector")["state"] == "active"
        report = {"passed": all(checks.values()), "checks": checks, "check_count": len(checks),
                  "live": service.network_live_state(), "receipts": receipts,
                  "scope": "Qt widget input and real simulated backend; no physical equipment or desktop mouse automation"}
    finally:
        if screen is not None:
            screen.shutdown(); screen.close(); screen.deleteLater()
        provider.stop(); app.processEvents()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
