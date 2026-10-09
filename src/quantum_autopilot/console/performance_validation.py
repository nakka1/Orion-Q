"""Reproducible diagnostic workload for the ordinary native console.

This module is shared by the source launcher and the frozen --performance-test
entry point. It exercises Qt widgets against the authenticated demonstration
provider and never touches the operator journal or starts physical acquisition.
Normal measurements and method/cProfile diagnostics use separate passes.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sqlite3
import sys
import time

WORKLOAD_REVISION = "actual-qt-actions-v3"
NORMAL_PAINT_SCOPE = "existing-python-paintEvent:stage+geo-renderers-v1"


def _normal_paint_target(receiver, stage_viewport, visual_types):
    return receiver is stage_viewport or isinstance(receiver, visual_types)


def _presentation_component_counts(windows):
    """Small status scalars only; no text corpus, snapshot, service or widget build."""
    statuses, unknown = [], 0
    for opened in windows.values():
        reader = getattr(opened, "presentation_status", None)
        if not callable(reader):
            unknown += 1
            continue
        status = reader()
        if type(status) is not dict or type(status.get("ready")) is not bool:
            unknown += 1
            continue
        statuses.append(status)
    pending = sum(bool(item.get("pending")) or item.get("ready") is False and
                  item.get("state") not in ("error", "failed", "closed") for item in statuses)
    ready = sum(item["ready"] for item in statuses)
    errors = sum(item.get("state") in ("error", "failed") for item in statuses)
    def maximum(name, *, require_coverage=False):
        values = [item[name] for item in statuses if type(item.get(name)) in (int, float)]
        return max(values, default=0) if not unknown and (not require_coverage or len(values) == len(statuses)) else None
    section_statuses = [item for item in statuses if isinstance(item.get("expected_sections"), (list, tuple))
        and isinstance(item.get("completed_sections"), (list, tuple))]
    incomplete_sections = [max(0, len(item["expected_sections"]) - len(item["completed_sections"]))
        for item in section_statuses]
    return {"node_windows_total": len(windows), "node_windows_status_supported": len(statuses),
        "node_windows_status_unknown": unknown,
        "node_windows_pending": pending if not unknown else None,
        "node_windows_ready": ready if not unknown else None,
        "node_windows_error": errors if not unknown else None,
        "node_windows_max_pending_items": maximum("pending_items"),
        "node_windows_max_step_ms": maximum("step_max_ms"),
        "node_windows_max_pending_core_rows": maximum("pending_core_rows", require_coverage=True),
        "node_windows_sections_supported": len(section_statuses),
        "node_windows_max_incomplete_sections": max(incomplete_sections, default=0)
            if not unknown and len(section_statuses) == len(statuses) else None}


def _cached_global_operation_counts(active):
    """Approximate O(1) lengths under the GIL, without locks, calls or set copies."""
    def data(obj):
        try:
            return object.__getattribute__(obj, "__dict__")
        except (AttributeError, TypeError):
            return {}
    service = data(active).get("service")
    engine = data(service).get("_network_engine")
    manager = data(engine).get("global_operations")
    fields = data(manager)
    def count(name):
        items = fields.get(name)
        return len(items) if type(items) in (set, list, tuple, dict) else None
    return {"global_manager_counters_available": manager is not None,
        "global_seen_receipt_sequences": count("_seen_receipt_sequences"),
        "global_claimed_quantum_receipts": count("_claimed_quantum_receipts"),
        "global_outbox_rows": count("_outbox")}


def _logical_geometry(widget):
    rect = widget.geometry()
    return {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height(),
            "device_pixel_ratio": widget.devicePixelRatioF()}


def _external_watchdog_budget(duration, soak):
    """Collection guard, not a product SLA or a change to scientific time."""
    regular = 4 * max(1, math.ceil(duration / 2))
    soak_commands = max(1, math.ceil(soak * 60 / 2)) if soak > 0 else 0
    planned_seconds = 4 * duration + soak * 60
    action_count = regular + soak_commands
    phase_allowance = planned_seconds + 150
    command_allowance = action_count * 6 + 90
    return {"schema": "orion.external-watchdog.v2", "enforced_by": "external launcher",
        "formula": "max(planned_duration_s + 150, planned_action_count * 6 + 90)",
        "planned_duration_s": planned_seconds, "planned_action_count": action_count,
        "phase_allowance_s": phase_allowance, "command_allowance_s": command_allowance,
        "per_command_collection_allowance_s": 6, "startup_collection_allowance_s": 90,
        "timeout_s": max(phase_allowance, command_allowance), "internal_readback_deadline_s": 5,
        "changes_product_acceptance_deadlines": False, "changes_scientific_time": False,
        "termination_scope": "freshly spawned diagnostic UI PID only"}


def _align_diagnostic_window(window):
    """Placement only, before sampling; use the actual screen, including negatives."""
    screen = window.screen()
    if screen is None:
        return {"applied": False, "reason": "screen unavailable"}
    rect = screen.geometry()
    window.move(rect.topLeft())
    return {"applied": True, "requested_screen_top_left": [rect.x(), rect.y()],
            "screen": screen.name()}


def _node_widget_content(opened):
    """Read materialized labels without building, finishing or refreshing a panel."""
    contents = {"title": opened.title.text(), "identity": opened.identity.texts(),
        "state": opened.state.texts(), "calibration": opened.calibration_kv.texts(),
        "calibration_links": opened.calibration_links.texts(), "routes": opened.routes.texts(),
        "legend": opened.legend_labels(), "cards": opened.connection_texts(),
        "status": opened.status.text()}
    if hasattr(opened, 'protocols'):contents['protocols'] = opened.protocols.texts()
    return contents


def _connection_expected(node_id, link, live_link):
    """Expected full connection values from received fields, not panel metadata."""
    from quantum_autopilot.console.widgets.inspector import dash, number, percent, rate
    from quantum_autopilot.console.widgets.network_views import format_km
    other = link.b if link.a == node_id else link.a
    result = {"link": link.key, "peer": f"↔ {other}"}
    if live_link is None:
        return {**result, "status": link.state_text.lower(), "Distance": format_km(link.distance_km),
            "Fidelity": link.fidelity, "QBER": link.qber, "Secret key rate": link.skr, "controllers": ""}
    result["status"] = live_link.status.replace("_", " ")
    detector = live_link.detector
    alarm = "ALARM" if detector.alarm else "no alarm" if detector.alarm is False else "—"
    technology = live_link.provenance.get("technology")
    if technology:
        metrics = live_link.provenance.get("protocol_metrics", {})
        result.update({"Distance": format_km(live_link.distance_km),
            "Fidelity estimate": number(live_link.fidelity, ".4f"), "Session": technology["session_id"],
            "Input origin": live_link.provenance.get("source", "unavailable")})
        if any(name in technology["technologies"] for name in ("tf_qkd", "satellite_doppler")):
            result.update({"QBER": percent(metrics.get("qber")),
                "Conditional budget": rate(metrics.get("conditional_key_estimate_bps")),
                "Certified key bits": str(metrics.get("certified_secret_bits", 0))})
            if "satellite_doppler" in technology["technologies"]:
                result["Coincidences"] = number(metrics.get("coincidences"), ".0f")
            else:
                result["Phase residual"] = number(metrics.get("phase_reference_residual_rad"), ".4f", "rad")
        else:
            result.update({"Pair generation": number(metrics.get("generation_pair_rate_hz"), ".3g", "pair/s"),
                "Pair delivery": number(metrics.get("delivered_pair_rate_hz"), ".3g", "pair/s"),
                "BSM success": percent(metrics.get("bsm_success_fraction")),
                "Purification yield": percent(metrics.get("purification_yield"))})
    else:
        result.update({"Distance": format_km(live_link.distance_km), "Fidelity": number(live_link.fidelity, ".4f"),
            "QBER": percent(live_link.qber), "Secret key rate": rate(live_link.skr_bps),
            "Sync residual": number(live_link.sync_residual_ps, ".2f", "ps"),
            "Polarisation residual": number(live_link.polarisation_residual_mrad, ".1f", "mrad"),
            "Stability": number(live_link.stability, ".3f"), "Detector": f"{dash(detector.label)} · {alarm}"})
    notes = [f"{item.role}: {item.controller_id}" + (f" ({item.model_state})" if item.model_state else "")
             for item in live_link.controllers]
    if technology:
        notes += ["technology: " + " + ".join(technology["technologies"]),
                  "session: " + live_link.provenance.get("session_status", "configured"),
                  live_link.provenance.get("security_status", "")]
    channels = live_link.provenance.get("channels", {})
    config = channels.get("configuration") or {}
    if config:
        notes.append(f"channels: {config['classical']['channel_id']} ↔ {config['quantum']['channel_id']} · "
                     f"{channels.get('status', 'no_input')} · software statistical ports; no physical transmission")
    elif channels:
        notes.append("channels: not configured; no communication attested")
    if channels.get("error"):
        notes.append("channel intake refused: " + channels["error"])
    if live_link.provenance.get("repeater_scope"):
        notes.append(live_link.provenance["repeater_scope"])
    if detector.state:
        notes.append(f"detector state: {detector.state}")
    if live_link.status_reasons:
        notes.append("status: " + "; ".join(live_link.status_reasons))
    result["controllers"] = "Controllers · " + " · ".join(notes) if notes else ""
    return result


def _received_node_fields(node, live_node, source, links):
    """Independent expected text from received data, including legacy renderers."""
    from quantum_autopilot.console.widgets.inspector import dash, number
    from quantum_autopilot.console.widgets.network_views import format_lat, format_lon
    role = live_node.role if live_node is not None else node.role
    kind = (live_node.kind if live_node is not None else node.kind) or "—"
    status = live_node.status if live_node is not None else f"{node.health} (no engine status)"
    located = live_node.located if live_node is not None else node.located
    latitude = live_node.latitude if live_node is not None else node.latitude
    longitude = live_node.longitude if live_node is not None else node.longitude
    place = (live_node.place if live_node is not None else node.place) or ""
    address = live_node.address if live_node is not None else getattr(source, "connection_address", None)
    port = live_node.port if live_node is not None else getattr(source, "connection_port", None)
    incident = live_node.links if live_node is not None else tuple(link.key for link in links)
    fields = {"id": node.id, "role": role, "kind": kind, "status": status,
        "located": "located" if located else "no location",
        "latitude": format_lat(latitude) if located else "no location",
        "longitude": format_lon(longitude) if located else "no location",
        "place": place or ("no place label" if located else "no location"),
        "links": ", ".join(incident) or "none",
        "address": dash(address) if address else "not supplied",
        "port": dash(port) if port is not None else "not supplied"}
    if live_node is None:
        missing = "not supplied by this source"
        fields.update(memory_state=missing, pairs=missing, temperature_c=missing,
            calibration_count=str(getattr(source, "calibration_count", 0)), routes_through=missing, calibration=missing)
    else:
        pairs, calibration = live_node.pairs, live_node.calibration
        fields.update(memory_state=dash(live_node.memory_state),
            pairs=f"{len(pairs)} stored · F " + ", ".join(f"{value:.3f}" for value in pairs) if pairs else "none stored",
            temperature_c=number(live_node.temperature_c, ".2f", "°C"), calibration_count=str(live_node.calibration_count),
            routes_through=", ".join(live_node.routes_through) or "none",
            calibration="none recorded" if calibration is None else
                f"{calibration.calibration_id} · {calibration.status} · {calibration.phase}")
    return fields


def _ground_widget_checks(opened, snap, net):
    """Check the displayed LiveNode fields, connections and received wave samples."""
    from quantum_autopilot.console.widgets.inspector import clock, dash, number, percent, rate
    node = net.node(opened.node_id)
    live = snap.live
    live_node = live.node(node.id) if live is not None else None
    source = next((item for item in snap.topology.nodes if item.id == node.id), None)
    links = tuple(net.links_of(node.id))
    fields = _received_node_fields(node, live_node, source, links)
    body = _node_widget_content(opened)
    expected_identity = {name: fields[field] for name, field in (
        ("Role", "role"), ("Kind", "kind"), ("Status", "status"), ("Place", "place"),
        ("Latitude", "latitude"), ("Longitude", "longitude"), ("Address", "address"), ("Port", "port"))}
    expected_identity["Isolation"] = "isolated — fibres out of the routing graph" if node.isolated else "not isolated"
    expected_state = {name: fields[field] for name, field in (
        ("Memory state · legacy inventory", "memory_state"), ("Stored pairs · legacy inventory", "pairs"),
        ("Temperature", "temperature_c"), ("Calibrations", "calibration_count"),
        ("Routes through", "routes_through"), ("Fibres", "links"), ("Location", "located"))}
    operations = (live.technologies or {}).get("global_operations", {}) if live is not None else {}
    component = next((item for item in operations.get("components", ()) if item.get("component") == node.id), {})
    if component:
        expected_state.update({"Operação": str(component.get("operation_state", "not_configured")).replace("_", " "),
            "Demandas ativas": str(component.get("active_demands", 0)),
            "Operações recebidas": str(component.get("successful_local_or_classical_operations", 0)),
            "Impedimentos": "; ".join(component.get("impediments", ())) or "nenhum registrado"})
    banks, occupied, unknown = [], 0, 0
    for ident, session in ((live.technologies or {}).get("sessions", {}).items() if live is not None else ()):
        config = session.get("config", {})
        if node.id not in config.get("path", ()) or "quantum_memory" not in config.get("technologies", ()):
            continue
        occupancy = session.get("metrics", {}).get("memory_qubit_occupancy")
        parameters = config.get("parameters", {})
        capacity = parameters.get("device_parameters", {}).get(node.id, {}).get("memory_capacity", parameters.get("memory_capacity"))
        current = occupancy.get(node.id, 0) if occupancy is not None else None
        occupied += current if current is not None else 0
        unknown += current is None
        banks.append((ident, ("aguardando aquisição" if current is None else f"{current} qubits ocupados") +
                      (f" / {capacity} modos" if capacity is not None else "") + f" · {session.get('status', 'configured')}"))
    if banks:
        expected_state.update({"Memória de protocolos": f"{occupied} qubits ocupados observados · {len(banks)} bancos por sessão" +
            (f" · {unknown} aguardando aquisição" if unknown else ""),
            "Bancos de memória": "; ".join(f"{ident}: {value}" for ident, value in banks)})
    checks = {"title": body["title"] == fields["id"],
        "live_node_metadata": opened.field_texts() == fields,
        "identity_widget_values": body["identity"] == expected_identity,
        "state_widget_values": body["state"] == expected_state}
    calibration = live_node.calibration if live_node is not None else None
    if calibration is None:
        expected_calibration = {"Last calibration": fields["calibration"]}
        expected_calibration_links = {}
    else:
        expected_calibration = {"Calibration": calibration.calibration_id,
            "Result": f"{calibration.status} · phase {calibration.phase}",
            "Completed": clock(calibration.completed_at) if calibration.completed_at else "—",
            "Duration": number(calibration.duration_s, ".0f", "s network time"),
            "Memory reset": "—" if calibration.memory_reset is None else "yes" if calibration.memory_reset else "no",
            "Report": (calibration.report_sha256 or "—")[:16] + ("…" if calibration.report_sha256 else "")}
        expected_calibration_links = {}
        for item in calibration.links:
            expected_calibration_links.update({f"{item.link_id} · QBER": f"{percent(item.qber_before)} → {percent(item.qber_after)}",
                f"{item.link_id} · sync": f"{number(item.sync_residual_ps_before, '.2f')} → {number(item.sync_residual_ps_after, '.2f', 'ps')}",
                f"{item.link_id} · after": f"{rate(item.key_rate_hz_after)} · {dash(item.status)}"})
    checks["calibration_widget_values"] = body["calibration"] == expected_calibration
    checks["calibration_links_widget_values"] = body["calibration_links"] == expected_calibration_links
    demands = [item for item in (live.demands if live is not None else ())
               if node.id in item.route or node.id in (item.a, item.b)]
    expected_routes = {f"{item.id} · {item.a} ↔ {item.b}": item.status.replace("_", " ") + " · " +
                       (" → ".join(item.route) if item.route else "no route") for item in demands}
    if not expected_routes:
        expected_routes = {"Demands": "none" if live is not None else "not supplied by this source"}
    checks["route_widget_values"] = body["routes"] == expected_routes
    live_links = {item.id: item for item in live.links} if live is not None else {}
    expected_cards = {link.key: _connection_expected(node.id, link, live_links.get(link.id)) for link in links}
    checks["connection_widget_values"] = body["cards"] == expected_cards
    expected_keys = [item.key for item in links]
    checks["legend_link_keys"] = list(opened._legend) == expected_keys and len(body["legend"]) == len(expected_keys)
    from quantum_autopilot.console.widgets.node_window import NO_WAVE_SOURCE
    managed_links = frozenset()
    if hasattr(opened, 'protocols'):
        from quantum_autopilot.console.widgets.protocol_observations import node_protocol_observations
        from quantum_autopilot.console.widgets.node_window import WAVE_CAPTION
        protocol_rows, managed_links = node_protocol_observations(node.id, live, links)
        checks['protocol_widget_values'] = body['protocols'] == {row[0]: row[1] for row in protocol_rows}
        checks['protocol_table_visibility'] = (not opened.protocols.isHidden()) == bool(protocol_rows)
        checks['protocol_heading_visibility'] = (not opened.protocol_label.isHidden()) == bool(protocol_rows)
        show_wave = bool([link for link in links if link.id not in managed_links]) or not links
        checks['chart_applicability'] = (not opened.chart.isHidden()) == show_wave
        checks['chart_measurement_scope'] = opened.wave_label.text().startswith(WAVE_CAPTION)
    expected_legend = []
    expected_notes = []
    legacy_links = tuple(link for link in links if link.id not in managed_links)
    for link in links:
        engine = live_links.get(link.id)
        other = link.b if link.a == node.id else link.a
        protocol = link.id in managed_links
        if protocol:
            note = 'métricas de protocolo na tabela da sessão'
            if engine is not None:note += ' · ' + engine.status.replace('_', ' ')
        else:note = NO_WAVE_SOURCE if engine is None else ""
        if not protocol and engine is not None and not engine.wave:
            note = "no trace · " + engine.status.replace("_", " ")
            if engine.status_reasons:
                note += " (" + "; ".join(engine.status_reasons) + ")"
        if not protocol:expected_notes.append(note)
        shown_note = note or engine.status.replace("_", " ")
        metric = ('sessão ' + str(engine.provenance.get('session_status', 'não publicada'))
                  if engine is not None else 'sessão não publicada') if protocol else (
                  "stability " + number(engine.stability if engine is not None else None, ".3f"))
        expected_legend.append(" · ".join((other, link.key, shown_note,
            metric)))
    checks["legend_widget_values"] = body["legend"] == expected_legend
    checks["chart_link_keys"] = opened.chart.keys() == [link.key for link in legacy_links]
    if hasattr(opened, 'protocols'):
        checks['legacy_legend_styles'] = all(opened._legend[link.key].swatch.index == index
                                           for index, link in enumerate(legacy_links))
        checks['protocol_legend_has_no_wave_swatch'] = all(opened._legend[link.key].swatch.isHidden()
                                                        for link in links if link.id in managed_links)
    checks["chart_received_values"] = all(
        series.key == link.key and series.label == f"{link.b if link.a == node.id else link.a} · {link.key}" and
        tuple(series.samples) == (tuple(live_links[link.id].wave) if link.id in live_links else ()) and
        series.rate_hz == (live_links[link.id].wave_rate_hz if link.id in live_links else 20.0)
        for series, link in zip(opened.chart.series, legacy_links)) and len(opened.chart.series) == len(legacy_links)
    checks["chart_source_notes"] = [series.note for series in opened.chart.series] == expected_notes
    checks["chart_network_time"] = opened.chart.network_time_s == (live.network_time_s if live is not None else None)
    return checks, body


def _satellite_widget_checks(opened, snap, net):
    """Check all materialized satellite tables against their received snapshot."""
    from quantum_autopilot.console.widgets.inspector import number, percent, rate
    from quantum_autopilot.console.widgets.satellite_telemetry import value
    state = (net.satellites or {}).get(opened.node_id, {})
    record = (net.communicators or {}).get(opened.node_id, {})
    panel = opened.satellite_panel
    bridges = [item for item in state.get("bridges", []) if "satellite_isl" not in item.get("technologies", [])]
    first = next((item for item in bridges if item.get("metrics")), bridges[0] if bridges else {})
    metrics = first.get("metrics", {})
    hk = state.get("housekeeping") or metrics.get("satellite_housekeeping", {}).get(
        state.get("node_id", state.get("device_id", ""))) or metrics.get("housekeeping", {})
    clock = state.get("clock_s")
    age = clock - hk["at_s"] if clock is not None and "at_s" in hk else None
    observed = bool(hk)
    stale = (clock is not None and hk.get("valid_until_s", float("inf")) < clock) or (
        observed and state.get("housekeeping_valid") is False)
    expected = {"orbital": {
        "Classe / modelo": f"{state.get('orbital_class', '—')} · {state.get('model', '—')}",
        "Tempo orbital": number(state.get("at_network_s"), ".3f", "s"), "Época UTC": value(state.get("at_utc")),
        "Idade / validade": f"{number(age, '.3f', 's')} / {number(hk.get('valid_until_s'), '.3f', 's da rede')}",
        "Altitude": number(state.get("altitude_km"), ".2f", "km"), "Posição ECEF · km": value(state.get("ecef_km")),
        "Velocidade · km/s": value(state.get("velocity_ecef_km_s")),
        "Escopo da posição": value(state.get("position_scope", "received observation" if observed else "configured ephemeris prediction")),
        "Housekeeping válido": value(state.get("housekeeping_valid", not stale if observed else None)),
        "Período": number(state.get("period_s"), ".1f", "s"),
        "Fonte orbital": value(state.get("ephemeris_source", record.get("orbit", {}).get("source")))}}
    statuses = {"observed_pairs": "Pares entre redes observados no último bloco", "waiting": "Aguardando observações válidas",
        "blocked": "Ponte indisponível; consulte o motivo do backend", "sampling": "Recepção em amostragem; sem coincidências",
        "disabled": "Sessão desativada"}
    rows = {}
    for index, bridge in enumerate(bridges, 1):
        data = bridge.get("metrics", {})
        fields = [(bridge["session_id"], " → ".join(bridge.get("path", []))),
            ("Protocolo / tecnologia", f"{bridge.get('protocol', '—')} · {' + '.join(bridge.get('technologies', []))}"),
            ("Estado da ponte", statuses.get(bridge.get("status"), bridge.get("status", "—"))),
            ("Motivo do backend", bridge.get("reason", "—")),
            ("Origem do bloco", f"{bridge.get('origin', 'sem observação')} · {data.get('observation_source_id', '—')}"),
            ("Coincidências / taxa", f"{value(data.get('coincidences'))} / {number(data.get('pair_rate_hz'), '.3g', 'pares/s')}"),
            ("Fidelidade conjunta", number(data.get("fidelity"), ".4f")),
            ("QBER / sifting", f"{percent(data.get('qber'))} / {value(data.get('sifted_bits'))} bits"),
            ("Orçamento condicional", rate(data.get("conditional_key_estimate_bps"))),
            ("Chave certificada", "0 · teste de protocolo; sem chave utilizável"),
            ("Aborto pós-processamento", value(data.get("finite_key", {}).get("abort_reason")))]
        for key, val in fields:
            label = f"{index} · {'Sessão / rota' if key == bridge['session_id'] else key}" if len(bridges) > 1 else key
            rows[label] = f"{bridge['session_id']} · {val}" if len(bridges) > 1 and key == bridge["session_id"] else val
    expected["bridge"] = rows or {"Conexão": "Nenhuma sessão satelital cadastrada"}
    rows = {}
    for index, link in enumerate(state.get("isl_links", []), 1):
        data = link.get("metrics", link.get("geometry", {}))
        geo = data.get("isl_geometry", data)
        c, q = link.get("classical", {}), link.get("quantum", {})
        budget = geo.get("link_budget_db", {})
        fields = [
            ("Enlace / destino", f"{link.get('link_id', '—')} ↔ {link.get('peer_id', '—')}"),
            ("Estado / causa", f"{link.get('status', link.get('state', '—'))} · {link.get('reason', '—')}"),
            ("Origem / tempo", f"{link.get('origin', geo.get('observation_origin', 'sem observação'))} · {number(link.get('at_s', geo.get('at_s')), '.3f', 's')}"),
            ("Escopo das métricas", link.get("geometry_scope", "Origem descrita pelo backend; hardware não qualificado")),
            ("Canal clássico", f"{c.get('channel_id', '—')} · {c.get('protocol', '—')} · {'ON' if c.get('enabled') else 'OFF'}"),
            ("Payload quântico", f"{q.get('channel_id', '—')} · {q.get('protocol', '—')} · {'ON' if q.get('enabled') else 'OFF'}"),
            ("Visibilidade / obstrução terrestre", f"{value(geo.get('visible'))} / {value(geo.get('earth_obstructed'))}"),
            ("Distância entre satélites", number(geo.get("range_km"), ".2f", "km")),
            ("Apontamento / sincronização", f"{number(geo.get('pointing_error_urad'), '.3g', 'µrad')} / {number(geo.get('sync_jitter_ps'), '.3g', 'ps')}"),
            ("Point-ahead / propagação", f"{number(geo.get('point_ahead_urad'), '.3g', 'µrad')} / {number(geo.get('flight_time_s'), '.4g', 's')}"),
            ("Doppler / residual", f"{number(geo.get('doppler_shift_hz'), '.4g', 'Hz')} / {number(geo.get('doppler_residual_hz'), '.4g', 'Hz')}"),
            ("Perda total / transmitância", f"{number(budget.get('total', geo.get('loss_db')), '.3g', 'dB')} / {number(geo.get('channel_transmission'), '.3g')}"),
            ("Emissões / detecções recebidas", f"{value(geo.get('emitted_photons', geo.get('emitted_pairs')))} / {value(geo.get('detected_photons', geo.get('detections')))}")]
        rows.update((f"ISL {index} · {key}", val) for key, val in fields)
    expected["isl"] = rows or {"ISL": "Nenhum enlace satélite–satélite cadastrado; proximidade não implica comunicação"}
    expected["operations"] = {f"Demanda {index}":
        f"{item.get('id', item.get('demand_id', '—'))} · {item.get('operation', '—')} · {item.get('state', item.get('status', '—'))} · {' → '.join(item.get('path', item.get('route', [])))} · {item.get('reason', '—')}"
        for index, item in enumerate(state.get("global_demands", []), 1)} or {
            "Demandas": "Nenhuma demanda atribuída a este satélite no snapshot"}
    expected_stations = {}
    transmitting = {item["link_id"] for item in state.get("transmitting_links", [])}
    for bridge in bridges:
        for item in bridge.get("connections", []):
            if item.get("endpoint_kind") == "satellite":
                continue
            endpoints, geometry = bridge.get("endpoints", []), bridge.get("metrics", {}).get("geometry", [])
            index = endpoints.index(item["station_id"]) if item["station_id"] in endpoints else -1
            geo = geometry[index] if 0 <= index < len(geometry) else {}
            c, q = item.get("classical", {}), item.get("quantum", {})
            expected_stations[item["link_id"]] = {
                "Associação": value(item.get("association_id")),
                "Canal clássico": f"{c.get('channel_id', '—')} · {c.get('protocol', '—')} · {'ON' if c.get('enabled') else 'OFF'}",
                "Payload quântico": f"{q.get('channel_id', '—')} · {q.get('protocol', '—')} · {'ON' if q.get('enabled') else 'OFF'}",
                "Emissão recebida": "Sim · último bloco recebido" if item["link_id"] in transmitting else "Não comprovada / indisponível",
                "Visibilidade": value(geo.get("visible")),
                "Elevação / azimute": f"{number(geo.get('elevation_deg'), '.2f', '°')} / {number(geo.get('azimuth_deg'), '.2f', '°')}",
                "Distância oblíqua": number(geo.get("range_km"), ".2f", "km"),
                "Doppler / residual": f"{number(geo.get('carrier_doppler_ghz'), '.4g', 'GHz')} / {number(geo.get('doppler_residual_hz'), '.4g', 'Hz')}",
                "Point-ahead": number(geo.get("point_ahead_urad"), ".3g", "µrad"),
                "Perda óptica total": number(geo.get("link_budget_db", {}).get("total"), ".3g", "dB")}
    body = {name: getattr(panel, name).texts() for name in expected}
    body["stations"] = {key: table.texts() for key, table in panel.stations.items()}
    body["title"] = opened.title.text()
    body["summary"] = panel.summary.text()
    checks = {"satellite_snapshot_values": panel._state == state, "title": body["title"] == opened.node_id,
        **{name + "_widget_values": body[name] == values for name, values in expected.items()},
        "station_widget_values": body["stations"] == expected_stations,
        "source_provenance_visible": f"Origem: {state.get('origin', 'sem dados')}" in body["summary"],
        "distinct_channels_visible": "RF/telemetria clássica e carga útil óptica quântica são canais distintos." in body["summary"]}
    return checks, body


def _node_presentation_evidence(opened, fallback_snap, fallback_net):
    """A loading window is feedback; only verified widget values are completion."""
    if opened is None:
        return {"state": "not_open", "ready": False, "complete": False, "checks": {}}
    status_reader = getattr(opened, "presentation_status", None)
    status = dict(status_reader()) if callable(status_reader) else {"state": "synchronous", "ready": True}
    mode = "asynchronous-contract" if callable(status_reader) else "legacy-synchronous-readback"
    result = {"state": status.get("state", "unknown"), "ready": status.get("ready") if type(status.get("ready")) is bool else None,
        "complete": False, "mode": mode, "presentation": status, "checks": {}}
    if not result["ready"]:
        return result
    checks = {}
    if callable(status_reader):
        checks["presentation_pending_empty"] = status.get("pending") == 0 and status.get("pending_items") == 0
        expected_links, completed_links = status.get("expected_link_keys"), status.get("completed_link_keys")
        checks["presentation_link_completion"] = isinstance(expected_links, (list, tuple)) and isinstance(completed_links, (list, tuple)) and (
            list(expected_links) == list(completed_links))
        section_fields = "expected_sections" in status or "completed_sections" in status
        result["section_status_supported"] = section_fields
        if section_fields:
            expected, completed = status.get("expected_sections"), status.get("completed_sections")
            checks["presentation_section_completion"] = isinstance(expected, (list, tuple)) and isinstance(completed, (list, tuple)) and (
                list(expected) == list(completed))
        if "pending_core_rows" in status:
            checks["presentation_core_rows_empty"] = status["pending_core_rows"] == 0
        if not all(checks.values()):
            result.update(state="incomplete_status", checks=checks,
                failed_checks=[key for key, passed in checks.items() if not passed])
            return result
        snapshot_reader = getattr(opened, "presentation_snapshot", None)
        presented = snapshot_reader() if callable(snapshot_reader) else None
        if not isinstance(presented, tuple) or len(presented) != 2:
            result["state"] = "unknown_snapshot"
            return result
        snap, net = presented
    else:
        snap, net = fallback_snap, fallback_net
    if snap is None or net is None or net.node(opened.node_id) is None:
        result["state"] = "unknown_snapshot"
        return result
    live = snap.live
    result.update({"presented_tick": live.tick if live else None, "source_kind": live.source_kind if live else None,
        "network_time_s": live.network_time_s if live else None})
    if net.node(opened.node_id).physical_type == "satellite":
        widget_checks, body = _satellite_widget_checks(opened, snap, net)
    else:
        widget_checks, body = _ground_widget_checks(opened, snap, net)
    checks.update(widget_checks)
    if callable(status_reader):
        checks["presentation_timestamp"] = status.get("presented_tick") == result["presented_tick"] and (
            status.get("source_kind") == result["source_kind"] and status.get("network_time_s") == result["network_time_s"])
    result["checks"] = checks
    result["complete"] = bool(checks and all(checks.values()))
    result["failed_checks"] = [key for key, passed in checks.items() if not passed]
    result["widget_content_sha256"] = hashlib.sha256(json.dumps(body, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    result["widget_field_counts"] = {key: len(value) for key, value in body.items() if isinstance(value, (dict, list))}
    return result


def _details_deadline_evidence(action_elapsed_s, checked_elapsed_s):
    observed_ms = max(0., (checked_elapsed_s - action_elapsed_s) * 1000)
    return {"readiness_deadline_ms": 5000., "readiness_observed_delay_ms": observed_ms,
        "readiness_deadline_exceeded": observed_ms > 5000.}


def _details_latency_summary(actions):
    selections = [item for item in actions if "functional_effect" in item]
    observed = [item for item in selections if type(item["functional_effect"].get("readiness_deadline_exceeded")) is bool]
    violations = [{"action": item["action"], "phase": item["phase"],
        "requested_id": item.get("requested_id"),
        "observed_delay_ms": item["functional_effect"]["readiness_observed_delay_ms"],
        "content_complete_and_corresponds": item["functional_effect"]["content_complete_and_corresponds"]}
        for item in observed if item["functional_effect"]["readiness_deadline_exceeded"]]
    return {"details_latency_limit_ms": 5000., "details_latency_checks": len(observed),
        "latency_deadline_violations": violations,
        "details_latency_limit_passed": not violations if observed and len(observed) == len(selections) else None,
        "details_latency_limit_affects_collection_exit": False}


def _install_paint_event_observers(monitor, stage_type, visual_types):
    """Measure existing Python virtuals without intercepting all Qt events.

    Native labels/layouts do not normally enter QApplication.notify in Python.
    A global override changes that property and can introduce GIL handoffs.
    Never add a Python virtual to a widget with a native paintEvent here.
    """
    changes, details = [], []
    for widget_type in (stage_type, *visual_types):
        original = getattr(widget_type, "paintEvent")
        if not hasattr(original, "__code__"):
            details.append({"class": widget_type.__name__, "observed": False, "reason": "native handler preserved"})
            continue
        had_own = "paintEvent" in widget_type.__dict__
        own_original = widget_type.__dict__.get("paintEvent")
        is_stage = widget_type is stage_type
        def measured(widget, event, _original=original, _is_stage=is_stage):
            monitor.paints_seen += 1
            monitor.paints_retained += 1
            started = time.perf_counter()
            try:
                return _original(widget, event)
            finally:
                monitor.paint(widget, started, (time.perf_counter() - started) * 1000,
                              stage_override=_is_stage)
        setattr(widget_type, "paintEvent", measured)
        changes.append((widget_type, had_own, own_original))
        details.append({"class": widget_type.__name__, "observed": True, "existing_python_handler": True})
    def restore():
        for widget_type, had_own, original in reversed(changes):
            if had_own:
                setattr(widget_type, "paintEvent", original)
            else:
                delattr(widget_type, "paintEvent")
    return restore, details


def _install_local_span_observers(monitor, specifications):
    """Time explicit calls only; preserve every call, exception and attribute."""
    from functools import wraps
    import threading
    changes = []
    for owner, name, label, metadata in specifications:
        original = getattr(owner, name, None)
        if not callable(original):
            continue
        had_own = name in owner.__dict__
        own_original = owner.__dict__.get(name)
        @wraps(original)
        def measured(*args, _original=original, _label=label, _metadata=metadata, **kwargs):
            details = _metadata(args, kwargs) if _metadata is not None else {}
            started, cpu_started = time.perf_counter(), time.thread_time()
            phase = monitor.phase
            try:
                return _original(*args, **kwargs)
            finally:
                end, cpu_end = time.perf_counter(), time.thread_time()
                monitor.method_samples.append({"method": _label, "thread": threading.current_thread().name,
                    "phase": phase, "elapsed_s": started - monitor.created, "end_elapsed_s": end - monitor.created,
                    "duration_ms": (end - started) * 1000, "thread_cpu_ms": (cpu_end - cpu_started) * 1000,
                    "thread_cpu_begin_s": cpu_started, "thread_cpu_end_s": cpu_end,
                    **details})
        setattr(owner, name, measured)
        changes.append((owner, name, had_own, own_original))
    def restore():
        for owner, name, had_own, original in reversed(changes):
            if had_own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
    return restore


def _local_profile_hooks(monitor):
    """Layout/show/GC diagnostics with original QApplication, no native virtuals."""
    from quantum_autopilot.console import ui_gc
    from quantum_autopilot.console.widgets import node_window, satellite_telemetry, inspector, cards, prims
    from quantum_autopilot.console.screens.topology import TopologyScreen
    from quantum_autopilot.console.screens.telemetry import TelemetryScreen
    from quantum_autopilot.console.screens.keys import KeysScreen
    from quantum_autopilot.console.widgets.live_qkd import LiveQkdPanel
    from quantum_autopilot.console.widgets.laboratory import LaboratoryPanel
    def gc_details(args, _kwargs):
        count = args[0]._count + 1
        generation = 2 if count % ui_gc.FULL_EVERY == 0 else 1 if count % ui_gc.MIDDLE_EVERY == 0 else 0
        return {"generation": generation, "collector_count": count}
    def rows_details(args, kwargs):
        rows = args[1] if len(args) > 1 else kwargs.get("rows")
        return {"row_count": len(rows) if type(rows) in (list, tuple) else None}
    specifications = []
    def add(owner, names, prefix, metadata=None):
        for name in names:
            specifications.append((owner, name, prefix + "." + name, metadata))
    add(ui_gc.UiThreadCollector, ("collect",), "UiThreadCollector", gc_details)
    add(node_window.NodeWindows, ("open", "close", "refresh"), "NodeWindows")
    add(node_window.NodeWindow, ("__init__", "refresh", "fit_host", "show"), "NodeWindow")
    add(node_window._FibreCard, ("__init__", "show_link"), "FibreCard")
    add(satellite_telemetry.SatelliteLivePanel, ("__init__", "set_state"), "SatelliteLivePanel")
    add(inspector.KVList, ("set_rows",), "inspector.KVList", rows_details)
    add(cards.KVList, ("set_rows",), "cards.KVList", rows_details)
    add(prims.KVRow, ("__init__", "set_value"), "KVRow")
    add(TopologyScreen, ("_open_window", "_render"), "TopologyScreen")
    add(TelemetryScreen, ("refresh", "_draw_chart"), "TelemetryScreen")
    add(KeysScreen, ("__init__", "refresh", "_ensure_live_panel"), "KeysScreen")
    add(LiveQkdPanel, ("__init__", "set_live"), "LiveQkdPanel")
    add(LaboratoryPanel, ("__init__", "set_snapshot"), "LaboratoryPanel")
    # Time factories as imported by each module. Native QLabel.resizeEvent is
    # deliberately untouched; adding a Python virtual would change the workload.
    for module in (prims, node_window, satellite_telemetry, inspector, cards):
        add(module, ("Prose", "L"), module.__name__.rsplit(".", 1)[-1])
    return _install_local_span_observers(monitor, specifications)


def _choose_combo_by_keyboard(combo, ident, click):
    """Open by pointer, navigate the visible popup and confirm with Enter.

    Qt blocks a popup's immediate mouse release after opening. A movement away
    from the opening point clears that guard, but native Windows mouse motion
    is asynchronous. Keyboard navigation preserves public UI interaction
    without inserting an artificial dwell into measured callback time.
    """
    from PySide6.QtCore import Qt, QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    key_count = 0
    def key(widget, value):
        nonlocal key_count
        key_count += 1
        # QTest's native keyboard route depends on active-window focus. The
        # measurement window intentionally does not steal the operator's focus;
        # direct Qt input events target only this known, visible popup.
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, value, Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, value, Qt.KeyboardModifier.NoModifier))
    index = combo.findData(ident)
    if index < 0:
        raise RuntimeError("Component missing from the GEO selector: " + ident)
    if combo.view().isVisible():
        key(combo.view(), Qt.Key.Key_Escape)
    started, cpu_started = time.perf_counter(), time.thread_time()
    click(combo)
    view = combo.view()
    if not view.isVisible():
        raise RuntimeError("GEO selector popup did not open")
    opened, cpu_opened = time.perf_counter(), time.thread_time()
    key(view, Qt.Key.Key_Home)
    for _ in range(index):
        key(view, Qt.Key.Key_Down)
    navigated, cpu_navigated = time.perf_counter(), time.thread_time()
    key(view, Qt.Key.Key_Return)
    committed, cpu_committed = time.perf_counter(), time.thread_time()
    if combo.currentData() != ident:
        raise RuntimeError("GEO selector did not choose " + ident)
    return {"target_index": index, "logical_keys": key_count, "key_events": key_count * 2,
        "popup_open_wall_ms": (opened - started) * 1000, "popup_open_thread_cpu_ms": (cpu_opened - cpu_started) * 1000,
        "navigation_wall_ms": (navigated - opened) * 1000, "navigation_thread_cpu_ms": (cpu_navigated - cpu_opened) * 1000,
        "commit_wall_ms": (committed - navigated) * 1000, "commit_thread_cpu_ms": (cpu_committed - cpu_navigated) * 1000}


def _action_plan(duration, soak, ground_ids, satellite_ids, pages):
    """Fixed work counts and targets: slow frames may delay, never skip work."""
    preferred = ("CIMATEC", "BR-CBPF", "CBPF", "IME-USP", "USP", "UNICAMP", "CIQUANTA")
    ground = [ident for ident in preferred if ident in ground_ids]
    ground += sorted(set(ground_ids) - set(ground))
    satellites = sorted(set(satellite_ids))
    if not ground or not satellites or not pages:
        raise ValueError("The workload requires ground endpoints, satellites and pages")
    count = max(1, math.ceil(duration / 2))
    result = {}
    node_index = satellite_index = 0
    for phase, seconds in [(name, duration) for name in ("map", "globe", "tabs", "swaps")] + (
            [("soak", soak * 60)] if soak > 0 else []):
        commands = []
        length = max(1, math.ceil(seconds / 2)) if phase == "soak" else count
        for index in range(length):
            command = {"index": index, "due_s": (index + .5) * seconds / length}
            if phase == "map":
                if index % 3 == 2:
                    command.update(action="map-scroll", angle=120 if index % 2 else -120, expected_mode="map")
                else:
                    command.update(action="map-node-hover-click", target=ground[node_index % len(ground)], expected_mode="map")
                    node_index += 1
            elif phase == "globe":
                if index % 4 == 3:
                    command.update(action="globe-scroll", angle=-120 if index % 2 else 120, expected_mode="globe")
                else:
                    command.update(action="satellite-hover-click", target=satellites[satellite_index % len(satellites)], expected_mode="globe")
                    satellite_index += 1
            elif phase == "tabs":
                command.update(action="page-" + pages[index % len(pages)], page=pages[index % len(pages)])
            elif phase == "swaps":
                command.update(action="scroll-mode-transition" if index % 2 else "button-mode-transition")
            elif index % 4 == 0:
                command.update(action="satellite-hover-click", target=satellites[satellite_index % len(satellites)], expected_mode="globe")
                satellite_index += 1
            elif index % 4 == 2:
                command.update(action="map-node-hover-click", target=ground[node_index % len(ground)], expected_mode="map")
                node_index += 1
            else:
                command.update(action="button-mode-transition", expected_mode="map" if index % 4 == 1 else "globe")
            commands.append(command)
        result[phase] = commands
    return {"revision": WORKLOAD_REVISION, "phases": result, "ground_ids": ground, "satellite_ids": satellites,
            "pages": list(pages), "minimum_phase_duration_s": duration, "soak_minutes": soak,
            "production_zoom_thresholds_modified": False}

def _collector_accounting(monitor, *, deep=False):
    """Record containers only; never Qt/provider state and never an RSS claim."""
    from collections import Counter
    fields = ("heartbeat_samples", "paint_samples", "component_samples", "actions", "errors", "snapshots",
              "method_samples", "pending_actions", "phase_records", "native_events", "pending_effects")
    roots = {name: getattr(monitor, name) for name in fields if type(getattr(monitor, name, None)) is list}
    counts = {name: {"rows": len(value), "list_shallow_bytes": sys.getsizeof(value)} for name, value in roots.items()}
    result = {"scope": "measurement collector record containers only", "deep": deep, "fields": counts,
        "list_shallow_bytes": sum(item["list_shallow_bytes"] for item in counts.values()),
        "excludes": ["Qt/native graphics", "backend/provider state", "worker processes", "allocator fragmentation", "audit/databases"],
        "not_process_rss": True, "cannot_establish_absence_of_leaks": True}
    if not deep:
        return result
    seen, kinds, skipped = set(), Counter(), Counter()
    def size(value):
        ident, kind = id(value), type(value)
        if ident in seen:
            return 0
        if kind not in (dict, list, tuple, str, int, float, bool, type(None)):
            skipped[kind.__name__] += 1
            return 0
        seen.add(ident)
        kinds[kind.__name__] += 1
        total = sys.getsizeof(value)
        if kind is dict:
            total += sum(size(key) + size(item) for key, item in value.items())
        elif kind in (list, tuple):
            total += sum(size(item) for item in value)
        return total
    for name, value in roots.items():
        counts[name]["unique_reachable_bytes_in_field_order"] = size(value)
    result.update(unique_reachable_bytes=sum(item["unique_reachable_bytes_in_field_order"] for item in counts.values()),
        unique_objects_by_type=dict(kinds), unsupported_references_by_type=dict(skipped),
        traversal_visited_set_shallow_bytes=sys.getsizeof(seen),
        timing_scope="after final measured phase; shared/interned records may also be owned elsewhere")
    return result


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def _archive_source_hashes(pyz, assets, manifest, harness_root):
    """Compiled before provenance without labeling changed source as before."""
    pyz, assets = Path(pyz).resolve(), Path(assets).resolve()
    result = {"@archived-pyz": hashlib.sha256(pyz.read_bytes()).hexdigest()}
    for path in sorted((assets / "quantum_autopilot").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml", ".csv", ".npz"}:
            result["@old-assets/" + path.relative_to(assets).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (Path(__file__), Path(harness_root) / "tools" / "benchmark_console_performance.py",
                 Path(harness_root) / "tools" / "benchmark_archive_importer.py"):
        result["@diagnostic/" + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["@old-source-manifest"] = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
    return result


def _source_hashes(root):
    import os
    if os.environ.get("ORION_BENCH_ARCHIVE_PYZ"):
        return _archive_source_hashes(os.environ["ORION_BENCH_ARCHIVE_PYZ"], os.environ["ORION_BENCH_ARCHIVE_ASSETS"],
            os.environ["ORION_BENCH_ARCHIVE_MANIFEST"], os.environ["ORION_BENCH_HARNESS_ROOT"])
    roots = [root / "src" / "quantum_autopilot"]
    if getattr(sys, "frozen", False):
        roots += [root / "quantum_autopilot", root / "configs"]
    paths = sorted({path for directory in roots for path in directory.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".py", ".json", ".yaml", ".yml", ".csv", ".npz"}})
    paths += [root / "pyproject.toml", root / "tools" / "benchmark_console_performance.py"]
    result = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths if path.is_file()}
    if getattr(sys, "frozen", False):
        # Bundled Python is compiled; executable provenance complements resource hashes.
        hasher = hashlib.sha256()
        with Path(sys.executable).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""):
                hasher.update(chunk)
        result["@frozen-executable"] = hasher.hexdigest()
    return result


def _versions():
    result = {"python": sys.version, "platform": platform.platform(), "processor": platform.processor()}
    for package in ("PySide6", "psutil", "numpy", "scipy", "cryptography", "torch", "quantum-twin"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def _summary(values):
    values = sorted(float(value) for value in values)
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "maximum": None, "mean": None}
    return {"count": len(values), **{name: values[min(len(values) - 1, int(len(values) * fraction))]
            for name, fraction in (("p50", .5), ("p95", .95), ("p99", .99))},
            "maximum": values[-1], "mean": sum(values) / len(values)}


def _csv(path, rows, columns):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _profile_hooks(monitor):
    """Opt-in diagnostic wrappers preserve the original call and every guard."""
    from functools import wraps
    import threading
    from quantum_autopilot.console.widgets import geo_map, orbit_globe, network_views, node_window
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.screens.topology import TopologyScreen
    from quantum_autopilot.console.screens.node import NodeScreen
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.stage import ResponsiveStage
    from quantum_autopilot.security.audit import AuthenticatedAudit
    from quantum_autopilot.security.runtime import SecurityRuntime
    from quantum_autopilot.console.screens.telemetry import TelemetryScreen
    from quantum_autopilot.console.screens.keys import KeysScreen
    from quantum_autopilot.console.screens.base import Screen
    from quantum_autopilot.console.widgets import cards
    from quantum_autopilot.console.widgets.live_qkd import LiveQkdPanel
    from quantum_autopilot.console.widgets.laboratory import LaboratoryPanel
    from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel

    def wrap(owner, name):
        original = getattr(owner, name, None)
        if not callable(original):
            return
        @wraps(original)
        def call(*args, **kwargs):
            started = time.perf_counter()
            cpu_started = time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                monitor.method_samples.append({"method": getattr(owner, "__name__", str(owner)) + "." + name,
                    "thread": threading.current_thread().name, "phase": monitor.phase,
                    "elapsed_s": started - monitor.created, "duration_ms": (time.perf_counter() - started) * 1000,
                    "thread_cpu_ms": (time.thread_time() - cpu_started) * 1000})
        setattr(owner, name, call)

    for owner, names in (
        (geo_map.MapRenderer, ("paintEvent", "_base_layer", "_map_paths", "_paint_surface", "segments", "hit")),
        (orbit_globe.OrbitGlobe, ("paintEvent", "_base_layer", "orbital_items", "ground_marker_geometry", "_states")),
        (network_views, ("matrix_grid",)), (network_views.MatrixModel, ("set_net",)),
        (network_views.MatrixView, ("set_model",)), (TopologyScreen, ("_render",)),
        (NodeScreen, ("_render_inventory", "_render", "set_selection")),
        (TopologyScreen, ("_select", "_open_window", "_inspector_select", "apply_command_capabilities")),
        (node_window.NodeWindow, ("__init__", "refresh", "fit_host")),
        (node_window.NodeWindows, ("open", "refresh", "close")),
        (node_window._FibreCard, ("__init__", "show_link")),
        (ConsoleWindow, ("_on_snapshot", "_set_selection", "select_node", "_screen_select", "show_screen")),
        (ResponsiveStage, ("_arrange", "paintEvent")),
        (TelemetryScreen, ("refresh", "_draw_chart", "_set_history")),
        (KeysScreen, ("refresh", "_ensure_live_panel")),
        (LiveQkdPanel, ("__init__", "set_live")), (LaboratoryPanel, ("set_snapshot",)),
        (SatelliteLivePanel, ("__init__", "set_state")),
        (cards.KVList, ("__init__", "set_rows", "resizeEvent")),
        (Screen, ("apply", "apply_command_capabilities")),
        (ServiceProvider, ("publish_snapshot", "_select_passively", "select_node", "select_link",
                           "_schedule_selection", "_prepare_publication", "_on_selection_completed")),
        (AuthenticatedAudit, ("_append",)), (SecurityRuntime, ("authorize",)),
    ):
        for name in names:
            wrap(owner, name)
    original = ServiceProvider.capabilities.fget
    @wraps(original)
    def capabilities(instance):
        started = time.perf_counter()
        cpu_started = time.thread_time()
        try:
            return original(instance)
        finally:
            monitor.method_samples.append({"method": "ServiceProvider.capabilities", "thread": threading.current_thread().name,
                "phase": monitor.phase, "elapsed_s": started - monitor.created,
                "duration_ms": (time.perf_counter() - started) * 1000,
                "thread_cpu_ms": (time.thread_time() - cpu_started) * 1000})
    ServiceProvider.capabilities = property(capabilities)


def _run_ui(args):
    root = Path(args.repository_root).resolve()
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QEvent, QPoint, QPointF, QTimer, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.app import build_parser, run
    from quantum_autopilot.console.display import configure_high_dpi
    from quantum_autopilot.console.widgets.network_views import build_net_model
    from quantum_autopilot.console.widgets.geo_map import MapRenderer
    from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe
    from quantum_autopilot.console.widgets.node_window import NodeWindow
    from quantum_autopilot.console.stage import ResponsiveStage

    class Monitor:
        def __init__(self):
            self.created = time.perf_counter()
            self.wall_origin = time.time()
            self.phase = "construction"
            self.phase_start = self.created
            self.window = self.app = None
            self.heartbeat_samples, self.paint_samples = [], []
            self.component_samples, self.actions, self.errors, self.snapshots = [], [], [], []
            self.method_samples, self.pending_actions, self.phase_records = [], [], []
            self.native_events = []
            self.collector_phase_samples = []
            self.stage_viewport = None
            self.paints_seen = self.paints_retained = self.paints_skipped = 0
            self.pending_effects = []
            self.first_snapshot = self.first_paint = None
            self.last_beat = None
            self.finished = self.started_phases = False
            self.action_index = 0
            self.next_action = 0.
            self.last_component = 0.
            self.phases = [(name, args.duration) for name in ("map", "globe", "tabs", "swaps")]
            if args.soak > 0:
                self.phases.append(("soak", args.soak * 60))
            self.phase_index = -1
            self._driving = False
            self.action_plan = None

        def attach(self, app, window):
            self.app, self.window = app, window
            self.stage_viewport = window.centralWidget().viewport()
            self.shown = time.perf_counter()
            self.phase = "startup"
            self.phase_start = self.shown
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            geometry_setup_started = time.perf_counter()
            window.resize(args.width, args.height)
            placement_started = time.perf_counter()
            self.initial_placement = _align_diagnostic_window(window)
            self.initial_placement.update({"phase": "startup", "outside_timed_phases": True,
                "elapsed_s": geometry_setup_started - self.created,
                "resize_duration_ms": (placement_started - geometry_setup_started) * 1000,
                "move_duration_ms": (time.perf_counter() - placement_started) * 1000,
                "duration_ms": (time.perf_counter() - geometry_setup_started) * 1000})
            window.provider.snapshot_changed.connect(self.snapshot)
            window.provider.error.connect(self.errors.append)
            self.heartbeat = QTimer(window)
            self.heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
            self.heartbeat.setInterval(10)
            self.heartbeat.timeout.connect(self.beat)
            self.heartbeat.start()
            self.driver = QTimer(window)
            self.driver.setInterval(100)
            self.driver.timeout.connect(self.drive)
            self.driver.start()

        def active(self):
            return getattr(self.window.provider, "active", self.window.provider)

        def snapshot(self, snap):
            now = time.perf_counter()
            if self.first_snapshot is None:
                self.first_snapshot = now
            live = snap.live
            self.snapshots.append({"elapsed_s": now - self.created, "phase": self.phase,
                "tick": live.tick if live else None, "nodes": len(snap.topology.nodes), "links": len(snap.topology.links),
                "satellites": len(live.satellites or {}) if live else 0,
                "technology_sessions": len(live.technologies.get("sessions", {})) if live else 0,
                "communicators": len(live.communicators or {}) if live else 0,
                "demands": len(live.demands) if live else 0,
                "global_demands": len(live.technologies.get("global_operations", {}).get("demands", [])) if live else 0,
                "source_kind": live.source_kind if live else None,
                "network_time_s": live.network_time_s if live else None})

        def beat(self):
            now = time.perf_counter()
            if self.last_beat is not None:
                self.heartbeat_samples.append({"elapsed_s": now - self.created, "phase": self.phase,
                                              "gap_ms": (now - self.last_beat) * 1000})
            self.last_beat = now
            if now - self.last_component >= .5:
                self.last_component = now
                self.components(now)

        def components(self, now):
            active = self.active()
            snap = self.window.snapshot
            topo = self.window.screens.get("topology")
            geo = getattr(topo, "geo", None)
            future = getattr(active, "_future", None)
            executor = getattr(active, "_executor", None)
            queue = getattr(executor, "_work_queue", None)
            worker = getattr(active, "_baseline_worker", None)
            live_future = getattr(active, "_live_future", None)
            live_queue = getattr(getattr(active, "_live_executor", None), "_work_queue", None)
            selection_queue = getattr(getattr(active, "_selection_executor", None), "_work_queue", None)
            live = snap.live if snap else None
            resources = geo.resource_stats() if geo else {}
            self.component_samples.append({"elapsed_s": now - self.created, "phase": self.phase,
                **_presentation_component_counts(topo.node_windows.windows if topo is not None else {}),
                **_cached_global_operation_counts(active),
                "screen": self.window.current_screen, "mode": geo.mode if geo else None,
                "nodes": len(snap.topology.nodes) if snap else 0, "links": len(snap.topology.links) if snap else 0,
                "satellites": len(live.satellites or {}) if live else 0,
                "tick": live.tick if live else None, "steps_taken": getattr(active, "steps_taken", None),
                "foreground_busy": self.window.provider.busy,
                "future_pending": future is not None and not future.done(),
                "worker_queue": queue.qsize() if queue is not None else None,
                "live_future_pending": live_future is not None and not live_future.done(),
                "live_worker_queue": live_queue.qsize() if live_queue is not None else None,
                "selection_worker_queue": selection_queue.qsize() if selection_queue is not None else None,
                "presentation_stats_json": json.dumps(getattr(snap, "presentation_stats", {}) if snap else {},
                                                      separators=(",", ":"), allow_nan=False),
                "baseline_pid": getattr(worker, "process_id", None),
                "renderer_instances": resources.get("renderer_instances"),
                "active_renderers": resources.get("active_renderers"),
                "created": resources.get("created"), "destroyed": resources.get("destroyed"),
                "orbit_timer_active": resources.get("orbit_timer_active"),
                "retiring": resources.get("retiring"), "transition_raster": resources.get("transition_raster"),
                "selected_kind": self.window.selection.kind, "selected_id": self.window.selection.id})

        def paint(self, receiver, start, duration_ms, *, stage_override=None):
            if self.window is None or self.finished:
                return
            is_stage = receiver is self.stage_viewport if stage_override is None else stage_override
            self.paint_samples.append({"elapsed_s": start - self.created, "phase": self.phase,
                "class": receiver.metaObject().className(), "object": receiver.objectName(),
                "duration_ms": duration_ms, "stage_viewport": is_stage})
            if is_stage:
                end = start + duration_ms / 1000
                if self.first_paint is None:
                    self.first_paint = end
                for item in self.pending_actions:
                    item["next_stage_paint_ms"] = (end - item.pop("started")) * 1000
                    item["screen_after"] = self.window.current_screen
                    item["selected_id_after"] = self.window.selection.id
                    item["mode_after"] = self.window.screens["topology"].geo.mode
                self.pending_actions.clear()

        def action(self, name, callback):
            now = time.perf_counter()
            item = {"action": name, "phase": self.phase, "elapsed_s": now - self.created,
                    "screen_before": self.window.current_screen, "started": now, "next_stage_paint_ms": None}
            item["mode_before"] = self.window.screens["topology"].geo.mode
            self.actions.append(item)
            self.pending_actions.append(item)
            try:
                callback()
                item["callback_ms"] = (time.perf_counter() - now) * 1000
                item["screen_after_dispatch"] = self.window.current_screen
                item["selected_id_after_dispatch"] = self.window.selection.id
                item["mode_after_dispatch"] = self.window.screens["topology"].geo.mode
                item["successful_dispatch"] = True
                if item.get("requested_id"):
                    self.pending_effects.append(item)
            except Exception as exc:
                item["successful_dispatch"] = False
                item["error"] = f"{type(exc).__name__}: {exc}"
                self.errors.append(item["error"])

        def ready_mode(self, mode):
            topo = self.window.screens["topology"]
            if self.window.current_screen != "topology":
                self.click(self.window.rail.buttons["topology"])
            if topo.geo.mode != mode:
                self.click(topo.geo.mode_button)
            deadline = time.perf_counter() + 5
            while topo.geo.renderer is None and time.perf_counter() < deadline:
                QTest.qWait(10)
            if topo.geo.mode != mode or topo.geo.renderer is None:
                raise RuntimeError("GEO renderer did not activate in " + mode)

        def combo_choose(self, combo, ident):
            details = _choose_combo_by_keyboard(combo, ident, self.click)
            self.actions[-1].setdefault("combo_interactions", []).append(details)

        def click(self, widget, position=None):
            if not widget.isVisible() or not widget.isEnabled():
                raise RuntimeError("Interaction target is hidden or disabled")
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                             pos=position.toPoint() if isinstance(position, QPointF) else position or widget.rect().center())

        def wheel(self, angle):
            geo = self.window.screens["topology"].geo
            target = geo.renderer
            if target is None:
                raise RuntimeError("Active GEO renderer is not ready")
            local = QPointF(target.rect().center())
            event = QWheelEvent(local, QPointF(target.mapToGlobal(local.toPoint())), QPoint(), QPoint(0, angle),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(target, event)

        def map_node(self, ident):
            action_stages = self.actions[-1].setdefault("stages", [])
            def stage(name, started, cpu_started):
                action_stages.append({"stage": name, "duration_ms": (time.perf_counter() - started) * 1000,
                    "thread_cpu_ms": (time.thread_time() - cpu_started) * 1000})
            started, cpu_started = time.perf_counter(), time.thread_time()
            self.ready_mode("map")
            stage("ensure_map_mode", started, cpu_started)
            topo = self.window.screens["topology"]
            started, cpu_started = time.perf_counter(), time.thread_time()
            for opened in list(topo.node_windows.windows.values()):
                self.click(opened.close_button)
            stage("close_previous_windows", started, cpu_started)
            self.actions[-1]["requested_id"] = ident
            # Campus selection uses actual widgets and the same public signal
            # as a user. It makes near-by real installations independently hitable.
            started, cpu_started = time.perf_counter(), time.thread_time()
            if topo.geo.focus_node.currentData() == ident:
                self.combo_choose(topo.geo.focus_node, "")
            self.combo_choose(topo.geo.focus_node, ident)
            stage("focus_campus_by_popup_input", started, cpu_started)
            renderer = topo.geo.renderer
            point = renderer.node_position(ident)
            if point is None or not renderer.rect().contains(point.toPoint()):
                raise RuntimeError("Campus node lies outside its local framing: " + ident)
            hit = renderer.hit(QPointF(point.toPoint()))
            self.actions[-1]["hit_target_before_click"] = list(hit)
            self.actions[-1]["marker_hit_matches_requested"] = hit == ("node", ident)
            started, cpu_started = time.perf_counter(), time.thread_time()
            QTest.mouseMove(renderer, point.toPoint())
            stage("marker_hover", started, cpu_started)
            started, cpu_started = time.perf_counter(), time.thread_time()
            self.click(renderer, point)
            stage("marker_click", started, cpu_started)

        def globe_satellite(self, ident):
            self.ready_mode("globe")
            topo = self.window.screens["topology"]
            for opened in list(topo.node_windows.windows.values()):
                self.click(opened.close_button)
            globe = topo.geo.globe
            items = list(globe.orbital_items()) if globe is not None else []
            if not items:
                raise RuntimeError("No backend satellite has a visible interaction handle")
            item = next((item for item in items if item["id"] == ident), None)
            if item is None:
                raise RuntimeError("Requested satellite lacks a visible handle: " + ident)
            QTest.mouseMove(globe, item["centre"].toPoint())
            self.actions[-1]["requested_id"] = item["id"]
            self.click(globe, item["centre"])

        def change_mode(self, *, scroll=False, expected=None):
            geo = self.window.screens["topology"].geo
            wanted = expected or ("globe" if geo.mode == "map" else "map")
            if geo.mode == wanted:
                raise RuntimeError("Planned mode change already in destination mode: " + wanted)
            gestures = 0
            if scroll:
                # Exercise existing hysteresis, regardless of prior local zoom;
                # never adjust the product thresholds to accommodate the test.
                while geo.mode != wanted and gestures < 24:
                    self.wheel(-960 if geo.mode == "map" else 960)
                    gestures += 1
                    QTest.qWait(10)
                self.actions[-1]["wheel_gestures"] = gestures
            else:
                self.click(geo.mode_button)
            self.ready_mode(wanted)
            self.actions[-1]["requested_mode"] = wanted
            self.actions[-1]["mode_transition_matches"] = geo.mode == wanted

        def dispatch_command(self, command):
            name = command["action"]
            if name == "map-node-hover-click":
                return self.map_node(command["target"])
            if name == "satellite-hover-click":
                return self.globe_satellite(command["target"])
            if name in ("map-scroll", "globe-scroll"):
                self.ready_mode(command["expected_mode"])
                self.wheel(command["angle"])
                self.actions[-1]["requested_mode"] = command["expected_mode"]
                self.actions[-1]["mode_transition_matches"] = self.window.screens["topology"].geo.mode == command["expected_mode"]
                return
            if name.startswith("page-"):
                self.click(self.window.rail.buttons[command["page"]])
                self.actions[-1]["requested_screen"] = command["page"]
                self.actions[-1]["screen_navigation_matches"] = self.window.current_screen == command["page"]
                return
            return self.change_mode(scroll=name == "scroll-mode-transition", expected=command.get("expected_mode"))

        def save_net_model(self):
            snap = self.window.snapshot
            path = Path(args.output) / "net-model.json"
            value = asdict(build_net_model(snap))
            _write_json(path, value)
            self.net_model = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "tick": snap.live.tick if snap.live else None, "nodes": len(value["nodes"]), "links": len(value["links"]),
                "satellites": len(value.get("satellites") or {}),
                "source_kind": snap.live.source_kind if snap.live else None,
                "captured_outside_steady_state_timings": True}
            _write_json(Path(args.output) / "scientific-context.json", {
                "source_kind": snap.live.source_kind if snap.live else None,
                "network_time_s": snap.live.network_time_s if snap.live else None,
                "technology_configurations": {ident: value.get("config", {}) for ident, value in
                    (snap.live.technologies.get("sessions", {}) if snap.live else {}).items()},
                "optional_models": [{"id": model.id, "state": model.state} for model in snap.live.models]
                    if snap.live else [], "physical_action_performed": False})
            self.action_plan = _action_plan(args.duration, args.soak,
                [node.id for node in self.window.screens["topology"]._net.nodes if node.located and
                 node.physical_type != "satellite" and node.network_role in ("endpoint", "core")],
                list(snap.live.satellites or {}), list(self.window.screens))
            plan_path = Path(args.output) / "action-plan.json"
            _write_json(plan_path, self.action_plan)
            self.action_plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()

        def begin_phase(self):
            now = time.perf_counter()
            if self.phase_index >= 0:
                self.phase_records[-1]["end_s"] = now - self.created
                self.collector_phase_samples.append({"phase": self.phase, "elapsed_s": now - self.created,
                    "outside_timed_phase": True, "accounting": _collector_accounting(self)})
            self.phase_index += 1
            if self.phase_index >= len(self.phases):
                self.finish()
                return
            self.phase, seconds = self.phases[self.phase_index]
            self.phase_start, self.action_index = now, 0
            self.phase_records.append({"phase": self.phase, "start_s": now - self.created, "duration_requested_s": seconds})
            topo = self.window.screens["topology"]
            if self.phase in ("map", "globe", "swaps", "soak"):
                if self.window.current_screen != "topology":
                    self.action("navigate-topology", lambda: self.click(self.window.rail.buttons["topology"]))
                wanted = "map" if self.phase == "map" else "globe"
                self.action("prepare-mode-for-" + self.phase, lambda: self.ready_mode(wanted))

        def drive(self):
            if self._driving or self.finished:
                return
            self._driving = True
            try:
                self._drive()
            finally:
                self._driving = False

        def _drive(self):
            now = time.perf_counter()
            self.check_effects(now)
            for item in list(self.pending_actions):
                if now - item["started"] > 5:
                    item["paint_deadline_exceeded"] = True
                    item.pop("started")
                    self.pending_actions.remove(item)
            if not self.started_phases:
                if self.first_snapshot is None:
                    if now - self.shown > 90:
                        self.errors.append("No snapshot within 90 seconds")
                        self.finish()
                    return
                active = self.active()
                if self.window.current_screen == "boot":
                    self.action("open-workspace", lambda: self.click(self.window.boot.open_button))
                    self.warmup_at = now
                    self.phase = "warmup"
                    return
                if getattr(active, "steps_taken", 0) < 3 or now - getattr(self, "warmup_at", now) < 2:
                    return
                self.save_net_model()
                self.started_phases = True
                self.begin_phase()
                return
            commands = self.action_plan["phases"][self.phase]
            if self.action_index >= len(commands):
                if now - self.phase_start >= self.phases[self.phase_index][1] and not self.pending_effects:
                    self.begin_phase()
                return
            command = commands[self.action_index]
            if now - self.phase_start < command["due_s"] or self.pending_effects:
                return
            self.action_index += 1
            self.action(command["action"], lambda: self.dispatch_command(command))
            self.actions[-1]["planned_command"] = command

        def finish(self):
            if self.finished:
                return
            self.finished = True
            self.finished_at = time.perf_counter()
            self.check_effects(self.finished_at, final=True)
            self.heartbeat.stop()
            self.driver.stop()
            if self.phase_records:
                self.phase_records[-1]["end_s"] = self.finished_at - self.created
            self.window.close()
            self.app.quit()

        def check_effects(self, now, *, final=False):
            # Cached UI/snapshot observations only; no backend requests here.
            topo = self.window.screens["topology"]
            for item in list(self.pending_effects):
                requested = item["requested_id"]
                opened = topo.node_windows.windows.get(requested)
                visible = bool(opened is not None and opened.isVisible() and opened.node_id == requested)
                matches = self.window.selection.id == requested and visible
                if visible:
                    item.setdefault("first_window_visible_elapsed_s", now - self.created)
                started = time.perf_counter()
                try:
                    receipt = _node_presentation_evidence(opened, getattr(topo, "_snap", None), topo._net)
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    receipt = {"state": "unknown_readback", "ready": None, "complete": False,
                        "error_type": type(exc).__name__, "checks": {}}
                item["presentation_checks"] = item.get("presentation_checks", 0) + 1
                item["presentation_verification_ms"] = item.get("presentation_verification_ms", 0) + (
                    time.perf_counter() - started) * 1000
                complete = matches and receipt["complete"]
                expired = now - self.created - item["elapsed_s"] > 5
                if complete or final or expired:
                    checked = time.perf_counter() - self.created
                    live = self.window.snapshot.live if self.window.snapshot else None
                    item["functional_effect"] = {
                        "requested_id": requested, "selected_id": self.window.selection.id,
                        "node_window_visible": visible, "selection_and_window_match": matches,
                        "content_complete_and_corresponds": complete,
                        "presentation_evidence": receipt, "timed_out": bool(expired and not complete),
                        "backend_tick": live.tick if live else None,
                        "source_kind": live.source_kind if live else None,
                        "mode": topo.geo.mode, "renderer_instances": topo.geo.resource_stats().get("renderer_instances"),
                        "checked_elapsed_s": checked,
                        **_details_deadline_evidence(item["elapsed_s"], checked)}
                    if complete:
                        item["presentation_verified_elapsed_s"] = checked
                        item["all_fields_verified_ms"] = (checked - item["elapsed_s"]) * 1000
                    else:
                        self.errors.append("Incomplete or inconsistent details for " + requested +
                            " (" + receipt["state"] + ")")
                    self.pending_effects.remove(item)

    class MeasuredApplication(QApplication):
        def notify(self, receiver, event):
            monitor = getattr(self, "benchmark_monitor", None)
            measured_paint = event.type() == QEvent.Type.Paint
            profiled_event = args.profile and event.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                QEvent.Type.LayoutRequest, QEvent.Type.UpdateRequest, QEvent.Type.Polish, QEvent.Type.PolishRequest)
            if monitor is None or not (measured_paint or profiled_event):
                return super().notify(receiver, event)
            if measured_paint:
                monitor.paints_seen += 1
                if not args.profile and not _normal_paint_target(receiver, monitor.stage_viewport,
                                                                  (MapRenderer, OrbitGlobe, NodeWindow)):
                    monitor.paints_skipped += 1
                    return super().notify(receiver, event)
                monitor.paints_retained += 1
            started = time.perf_counter()
            cpu_started = time.thread_time() if args.profile else None
            try:
                return super().notify(receiver, event)
            finally:
                wall_ms = (time.perf_counter() - started) * 1000
                if measured_paint:
                    monitor.paint(receiver, started, wall_ms)
                if args.profile and wall_ms >= 1:
                    parents, ancestor = [], receiver
                    for _ in range(6):
                        if ancestor is None:
                            break
                        parents.append({"class": ancestor.metaObject().className(), "object": ancestor.objectName()})
                        ancestor = ancestor.parent()
                    monitor.native_events.append({"event": event.type().name, "phase": monitor.phase,
                        "elapsed_s": started - monitor.created, "duration_ms": wall_ms,
                        "thread_cpu_ms": (time.thread_time() - cpu_started) * 1000, "ancestry": parents})

    configure_high_dpi()
    # Normal passes use the same QApplication type as the product. The global
    # notify override is intentionally restricted to explicit profile passes.
    app = MeasuredApplication(sys.argv) if args.profile else QApplication(sys.argv)
    monitor = Monitor()
    restore_paints, paint_handlers = (None, [])
    if args.profile:
        app.benchmark_monitor = monitor
    else:
        restore_paints, paint_handlers = _install_paint_event_observers(
            monitor, ResponsiveStage, (MapRenderer, OrbitGlobe, NodeWindow))
    profiler = None
    local_profile = bool(getattr(args, "local_profile", False))
    restore_spans = _local_profile_hooks(monitor) if local_profile else None
    if args.profile:
        import cProfile
        _profile_hooks(monitor)
        profiler = cProfile.Profile()
        profiler.enable()
    native_args = build_parser().parse_args(["--provider", "demo", "--repository-root", str(root),
                                             "--state-root", str(Path(args.output) / "state")])
    original_switch_interval = sys.getswitchinterval()
    requested_interval = getattr(args, "switch_interval", None)
    if requested_interval is not None:
        sys.setswitchinterval(requested_interval)
    active_switch_interval = sys.getswitchinterval()
    try:
        code = run(native_args, startup_monitor=monitor)
    finally:
        sys.setswitchinterval(original_switch_interval)
        if restore_paints is not None:
            restore_paints()
        if restore_spans is not None:
            restore_spans()
        if profiler is not None:
            profiler.disable()
            profiler.dump_stats(str(Path(args.output) / "ui.cprofile"))
    out = Path(args.output)
    collector_accounting = _collector_accounting(monitor, deep=True)
    _write_json(out / "collector-accounting.json", {"end": collector_accounting, "phase_boundaries": monitor.collector_phase_samples})
    for item in monitor.actions:
        item.pop("started", None)
    _derive_after_dispatch_paints(monitor.actions, monitor.paint_samples)
    _csv(out / "heartbeat.csv", monitor.heartbeat_samples, ("elapsed_s", "phase", "gap_ms"))
    _csv(out / "paints.csv", monitor.paint_samples, ("elapsed_s", "phase", "class", "object", "duration_ms", "stage_viewport"))
    _csv(out / "components.csv", monitor.component_samples,
         tuple(monitor.component_samples[0]) if monitor.component_samples else ("elapsed_s", "phase"))
    _write_json(out / "actions.json", monitor.actions)
    _write_json(out / "snapshots.json", monitor.snapshots)
    if args.profile or local_profile:
        _csv(out / "methods.csv", monitor.method_samples, ("method", "thread", "phase", "elapsed_s", "end_elapsed_s",
            "duration_ms", "thread_cpu_ms", "thread_cpu_begin_s", "thread_cpu_end_s",
            "generation", "collector_count", "row_count"))
    if args.profile:
        _write_json(out / "native-events.json", monitor.native_events)
    active = monitor.active()
    service = getattr(active, "service", None)
    persisted = {}
    databases = list((out / "state").rglob("*.sqlite")) + list((out / "state").rglob("*.sqlite3")) + list((out / "state").rglob("*.db"))
    for path in databases:
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                persisted[str(path.relative_to(out))] = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names}
        except sqlite3.Error as exc:
            persisted[str(path.relative_to(out))] = {"read_error": type(exc).__name__}
    audit = service.security.audit.verify() if service is not None else None
    phases = {}
    for phase, _seconds in monitor.phases:
        beats = [row["gap_ms"] for row in monitor.heartbeat_samples if row["phase"] == phase]
        paints = [row["duration_ms"] for row in monitor.paint_samples if row["phase"] == phase and row["stage_viewport"]]
        phase_actions = [row for row in monitor.actions if row["phase"] == phase]
        phases[phase] = {"heartbeat_ms": _summary(beats), "stage_paint_ms": _summary(paints),
            "action_to_next_stage_paint_ms": _summary([row["next_stage_paint_ms"] for row in phase_actions if row["next_stage_paint_ms"] is not None]),
            "action_callback_ms": _summary([row["callback_ms"] for row in phase_actions if "callback_ms" in row]),
            "action_to_paint_after_dispatch_ms": _summary([row["paint_after_dispatch_ms"] for row in phase_actions
                if row.get("paint_after_dispatch_ms") is not None]),
            "action_to_all_fields_verified_ms": _summary([row["all_fields_verified_ms"] for row in phase_actions
                if row.get("all_fields_verified_ms") is not None]),
            "action_to_complete_paint_begin_ms": _summary([row["paint_after_presentation_ms"] for row in phase_actions
                if row.get("paint_after_presentation_ms") is not None]),
            "gaps_over_100_ms": sum(value > 100 for value in beats), "gaps_over_250_ms": sum(value >= 250 for value in beats),
            "actions": len(phase_actions), "failed_dispatches": sum(not row["successful_dispatch"] for row in phase_actions)}
        planned = [item for item in phase_actions if "planned_command" in item]
        phases[phase]["planned_actions_expected"] = len(monitor.action_plan["phases"][phase]) if monitor.action_plan else None
        phases[phase]["planned_actions_completed"] = len(planned)
        phase_components = [row for row in monitor.component_samples if row["phase"] == phase]
        phases[phase]["node_presentation_components"] = {name: _summary([row[name] for row in phase_components
            if type(row.get(name)) in (int, float)]) for name in (
                "node_windows_total", "node_windows_status_supported", "node_windows_status_unknown",
                "node_windows_pending", "node_windows_ready", "node_windows_error",
                "node_windows_max_pending_items", "node_windows_max_step_ms",
                "node_windows_max_pending_core_rows", "node_windows_sections_supported",
                "node_windows_max_incomplete_sections")}
        phases[phase]["global_operation_counts"] = {}
        for name in ("global_seen_receipt_sequences", "global_claimed_quantum_receipts", "global_outbox_rows"):
            values = [row[name] for row in phase_components if type(row.get(name)) in (int, float)]
            phases[phase]["global_operation_counts"][name] = {"first": values[0] if values else None,
                "last": values[-1] if values else None, "maximum": max(values) if values else None,
                "observations": len(values), "missing_observations": len(phase_components) - len(values)}
    latest = monitor.window.snapshot
    screen = monitor.window.screen()
    screen_geometry = screen.geometry() if screen is not None else None
    workarea = screen.availableGeometry() if screen is not None else None
    frame_geometry = monitor.window.frameGeometry()
    report = {"schema": "orion.console-performance.v1", "mode": "profile" if args.profile else
        "local-profile" if local_profile else "measurement",
        "exit_code": code, "errors": monitor.errors, "phases": phases, "phase_windows": monitor.phase_records,
        "platform": app.platformName(), "qt_version": pyside_version,
        "ui_process_id": __import__("os").getpid(),
        "client_size": [args.width, args.height], "device_pixel_ratio": monitor.window.devicePixelRatioF(),
        "requested_client_size": [args.width, args.height],
        "initial_window_placement": monitor.initial_placement,
        "actual_client_size": [monitor.window.width(), monitor.window.height()],
        "actual_window_geometry": _logical_geometry(monitor.window),
        "actual_frame_geometry": {"x": frame_geometry.x(), "y": frame_geometry.y(),
            "width": frame_geometry.width(), "height": frame_geometry.height()},
        "actual_stage_viewport_geometry": _logical_geometry(monitor.stage_viewport),
        "screen_geometry": {"name": screen.name(), "width": screen_geometry.width(), "height": screen_geometry.height(),
            "x": screen_geometry.x(), "y": screen_geometry.y(), "device_pixel_ratio": screen.devicePixelRatio(),
            "available": {"x": workarea.x(), "y": workarea.y(), "width": workarea.width(), "height": workarea.height()}}
            if screen is not None else None,
        "external_watchdog": getattr(args, "external_watchdog", None),
        "time_origin_unix": monitor.wall_origin,
        "first_snapshot_after_show_s": monitor.first_snapshot - monitor.shown if monitor.first_snapshot else None,
        "first_stage_paint_after_show_s": monitor.first_paint - monitor.shown if monitor.first_paint else None,
        "construction_to_attach_s": monitor.shown - monitor.created,
        "component_counts": monitor.snapshots[-1] if monitor.snapshots else None,
        "net_model": getattr(monitor, "net_model", None), "persisted_table_counts": persisted,
        "authenticated_audit": audit, "provider_step_period_s": getattr(active, "step_period_s", None),
        "presentation_stats": dict(getattr(latest, "presentation_stats", {}) if latest else {}),
        "physical_action_performed": False,
        **_details_latency_summary(monitor.actions),
        "functional_effect_checks": {
            "selections": sum("functional_effect" in item for item in monitor.actions),
            "mismatches": [item["functional_effect"] for item in monitor.actions
                if "functional_effect" in item and not item["functional_effect"]["content_complete_and_corresponds"]],
            "complete_content_readbacks": sum(item.get("functional_effect", {}).get("content_complete_and_corresponds", False)
                for item in monitor.actions),
            "mode_transitions": sum("requested_mode" in item for item in monitor.actions),
            "actual_mode_changes": sum(item.get("mode_before") != item.get("mode_after_dispatch") for item in monitor.actions
                if "mode_after_dispatch" in item),
            "planned_mode_changes": sum("planned_command" in item and item["action"] in
                ("button-mode-transition", "scroll-mode-transition") and item.get("mode_before") != item.get("mode_after_dispatch")
                for item in monitor.actions),
            "mode_transition_mismatches": [item for item in monitor.actions if item.get("mode_transition_matches") is False],
            "page_navigations": sum("requested_screen" in item for item in monitor.actions),
            "page_navigation_mismatches": [item for item in monitor.actions if item.get("screen_navigation_matches") is False],
            "marker_hit_mismatches": [item for item in monitor.actions if item.get("marker_hit_matches_requested") is False]},
        "measurement_scope": "actual app.run + native Windows Qt + simulated source; Qt widget events, no OS input injection",
        "details_readback_scope": "all-ground-fields+connections+received-wave and all-satellite-tables-v1",
        "details_completion_is_separate_from_first_feedback": True,
        "component_counters_scope": {"presentation": "cached-status-scalars-only-v1",
            "global_operations": "direct-built-in-lengths-no-lock-no-copy-v1",
            "global_counts_are_approximate_not_an_atomic_backend_snapshot": True,
            "unavailable_legacy_or_remote_counters_are_null": True},
        "normal_measurement_has_method_profiling_hooks": bool(args.profile or local_profile),
        "localized_profile": {"enabled": local_profile, "scope": "explicit show/layout/factory/GC calls-v1",
            "cprofile_enabled": bool(args.profile), "global_notify_override": bool(args.profile),
            "native_label_resize_overridden": False, "classes_restored": restore_spans is not None if local_profile else None},
        "paint_recording": {"scope": "all-widget-profile" if args.profile else NORMAL_PAINT_SCOPE,
            "seen": monitor.paints_seen, "retained": monitor.paints_retained, "skipped": monitor.paints_skipped,
            "stage_duration_includes_children": True, "raw_latency_derivation_preserved": True,
            "global_python_notify_override": bool(args.profile),
            "seen_is_observed_handlers_only": not args.profile,
            "unobserved_native_paints_counted": bool(args.profile),
            "handlers": paint_handlers},
        "optional_models": [{"id": model.id, "state": model.state} for model in latest.live.models] if latest and latest.live else [],
        "source_hashes_start": args.source_hashes_start, "source_hashes_end": _source_hashes(root),
        "versions": _versions(), "final_soak_at_least_three_minutes": args.soak >= 3,
        "frozen": bool(getattr(sys, "frozen", False)),
        "thread_switch_interval_s": active_switch_interval,
        "original_thread_switch_interval_s": original_switch_interval,
        "thread_switch_interval_restored": sys.getswitchinterval() == original_switch_interval,
        "collector_accounting": {"file": "collector-accounting.json", "unique_reachable_python_bytes":
            collector_accounting["unique_reachable_bytes"], "sampled_after_timed_phases": True, "not_process_rss": True},
        "workload_revision": WORKLOAD_REVISION,
        "action_plan_sha256": getattr(monitor, "action_plan_sha", None),
        "code_origin": "archived-compiled-before" if __import__("os").environ.get("ORION_BENCH_ARCHIVE_PYZ") else
            "frozen" if getattr(sys, "frozen", False) else "current-source"}
    if __import__("os").environ.get("ORION_BENCH_ARCHIVE_PYZ"):
        from benchmark_archive_importer import provenance
        report["archive_provenance"] = provenance()
    report["source_unchanged"] = report["source_hashes_start"] == report["source_hashes_end"]
    report["measurement_completed"] = bool(code == 0 and not monitor.errors and report["source_unchanged"] and monitor.started_phases)
    _write_json(out / "report.json", report)
    return 0 if report["measurement_completed"] else 1



def _derive_after_dispatch_paints(actions, paints):
    """Add a post-dispatch measure without changing the original first paint.

    QTest can enter a nested event loop while closing a previous details panel.
    The first stage paint can then show the previous selection. Raw baseline
    samples permit deriving the first paint starting after the complete click.
    """
    stage = sorted((row for row in paints if row["stage_viewport"] in (True, "True")),
                   key=lambda row: float(row["elapsed_s"]))
    for action in actions:
        after = float(action["elapsed_s"]) + action.get("callback_ms", 0) / 1000
        paint = next((row for row in stage if float(row["elapsed_s"]) >= after), None)
        action["paint_after_dispatch_ms"] = (
            (float(paint["elapsed_s"]) - float(action["elapsed_s"])) * 1000 if paint else None)
        verified = action.get("presentation_verified_elapsed_s")
        complete_paint = next((row for row in stage if verified is not None and
            float(row["elapsed_s"]) >= max(after, verified)), None)
        action["paint_after_presentation_ms"] = (
            (float(complete_paint["elapsed_s"]) - float(action["elapsed_s"])) * 1000 if complete_paint else None)


def performance_options(output, *, duration=20, soak=3, size="3440x1440", profile=False, local_profile=False,
                        repository_root=None):
    """Validate before constructing Qt or creating a diagnostic directory."""
    from quantum_autopilot.desktop import bundled_repository_root
    if profile and local_profile:
        raise ValueError("global and localized profile passes must be separate")
    if not math.isfinite(duration) or duration < 1 or not math.isfinite(soak) or soak < 0:
        raise ValueError("duration must be >=1 second and soak >=0 minutes")
    try:
        width, height = (int(value) for value in size.lower().split("x"))
    except (TypeError, ValueError):
        raise ValueError("size must be WIDTHxHEIGHT") from None
    if not (320 <= width <= 7680 and 240 <= height <= 4320):
        raise ValueError("size is outside 320x240..7680x4320")
    return argparse.Namespace(output=Path(output).resolve(), duration=duration, soak=soak,
                              size=size, width=width, height=height, profile=profile, local_profile=local_profile,
                              repository_root=Path(repository_root or bundled_repository_root()).resolve())


def run_performance_test(desktop_args):
    """Frozen/source CLI entry: always fresh diagnostic state, ordinary auth."""
    args = performance_options(desktop_args.performance_test,
        duration=desktop_args.performance_duration, soak=desktop_args.performance_soak,
        size=desktop_args.performance_size, profile=desktop_args.performance_profile,
        local_profile=getattr(desktop_args, "performance_local_profile", False),
        repository_root=desktop_args.repository_root)
    output = args.output
    config_path = output / "configuration.json"
    allowed_launcher_files = {"configuration.json", "stdout.log", "stderr.log", "resources.jsonl", "gpu-context.jsonl"}
    if output.exists():
        # The external watchdog writes only these files before launching us.
        # Refuse an existing state/report and every unrelated directory/file.
        if (not config_path.is_file() or any(path.name not in allowed_launcher_files
                                            or not path.is_file() for path in output.iterdir())):
            raise ValueError("performance output must be a new isolated directory")
        configuration = json.loads(config_path.read_text(encoding="utf-8"))
        if configuration.get("schema") != "orion.console-performance-config.v1":
            raise ValueError("invalid performance launcher configuration")
    else:
        output.mkdir(parents=True, exist_ok=False)
        configuration = {"schema": "orion.console-performance-config.v1",
            "duration_s": args.duration, "soak_minutes": args.soak, "size": [args.width, args.height],
            "profile": args.profile, "local_profile": args.local_profile,
            "provider": "demo", "protocol_parameters_modified": False,
            "heartbeat_interval_ms": 10, "source_hashes_start": _source_hashes(args.repository_root),
            "versions": _versions(), "external_sampler_expected": False}
        _write_json(config_path, configuration)
    expected = {"duration_s": args.duration, "soak_minutes": args.soak,
                "size": [args.width, args.height], "profile": args.profile, "local_profile": args.local_profile}
    if any(configuration.get(key, False if key == "local_profile" else None) != value for key, value in expected.items()):
        raise ValueError("performance options differ from the launcher configuration")
    args.external_watchdog = configuration.get("external_watchdog")
    args.source_hashes_start = _source_hashes(args.repository_root)
    configuration["runtime_source_hashes_start"] = args.source_hashes_start
    configuration["frozen"] = bool(getattr(sys, "frozen", False))
    _write_json(config_path, configuration)
    return _run_ui(args)
