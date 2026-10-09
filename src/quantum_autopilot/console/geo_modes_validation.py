"""GEO acceptance in source/frozen Qt using one actual backend observation.

No provider subscription, protocol computation or hardware command is created
by this diagnostic. It drives the same controls as the operator workspace.
"""
import json
from pathlib import Path


def validate_geo_modes(destination, net):
    from PySide6.QtCore import QPoint, QPointF, QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import shiboken6
    from .widgets.geo_map import GeoMap, MapRenderer
    from .widgets.geo_view import MAP_TO_GLOBE_SPAN_M, GLOBE_TO_MAP_SPAN_M
    from .widgets.orbit_globe import OrbitGlobe
    from .widgets.chrome import IconRail

    app = QApplication.instance(); out = Path(destination); out.mkdir(parents=True, exist_ok=True)
    checks = {}; rows = []; geo = GeoMap(); rail = IconRail()
    report = {'schema': 'orion.geo-modes-validation.v1', 'passed': False, 'checks': checks,
              'qt_platform': app.platformName(), 'cases': rows, 'physical_action_performed': False,
              'input_delivery': 'QPA mouse-move events to the diagnostic QWindow; no OS cursor positioning dependency',
              'scope': 'Qt interactions with a single actual simulated-backend snapshot; physical mouse/independent workstation not qualified'}

    def settle():
        app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents(); QTest.qWait(3)

    def check(key, result, detail):
        checks[key] = bool(result); rows.append({'case': key, 'passed': bool(result), 'detail': detail})

    def wheel(delta):
        renderer = geo.renderer; point = QPointF(renderer.width()/2, renderer.height()/2)
        event = QWheelEvent(point, QPointF(renderer.mapToGlobal(point.toPoint())), QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(renderer, event); settle()

    try:
        geo.resize(1280, 900); geo.set_model(net); geo.set_mode('map'); geo.show(); settle()
        geo.raise_(); geo.activateWindow(); QTest.qWait(30)
        before = geo.renderer; geo.mode_button.click(); settle()
        check('exclusive_renderer_activation', not shiboken6.isValid(before) and isinstance(geo.renderer, OrbitGlobe), 'Map QObject destroyed before globe activation')
        expected = {node.id for node in net.nodes if node.physical_type == 'satellite' and (net.satellites or {}).get(node.id, {}).get('ecef_km')}
        expected_orbits = {node.id for node in net.nodes if node.physical_type == 'satellite' and (net.satellites or {}).get(node.id, {}).get('orbit_ecef_km')}
        items = geo.globe.orbital_items()
        check('all_published_satellites_and_orbits', bool(expected) and {i['id'] for i in items} == expected and set(geo.globe._orbit_paths) == expected_orbits,
              {'satellites': sorted(expected), 'orbits': sorted(expected_orbits)})
        # The self-test has other standalone forms open. Activate this test
        # window and settle its raster transition before OS-routed hover events.
        geo.raise_(); geo.activateWindow(); QTest.qWait(400)
        signal_ids = []; geo.node_selected.connect(signal_ids.append)
        camera = geo.globe.camera_state(); selected_ok = True; interactions = []
        for item in items:
            # QWidget's QTest overload may use QCursor::setPos, which does not
            # guarantee a move event with an offscreen platform/overlapping
            # windows. The QWindow overload injects QPA events and preserves
            # normal child-widget hit testing and mouse tracking.
            QTest.mouseMove(geo.windowHandle(), item['centre'].toPoint()); app.processEvents()
            hovered = geo.globe.hovered_id
            hover_ok = hovered == item['id']
            selected_ok &= hover_ok and item['hit'].width() >= 48 and item['size_px'] >= 32
            QTest.mouseClick(geo.globe, Qt.MouseButton.LeftButton, pos=item['centre'].toPoint())
            camera_ok = geo.globe.camera_state() == camera
            selected_ok &= geo.globe.satellite_id == item['id'] and camera_ok and len(geo.globe.orbital_items()) == len(expected)
            interactions.append({'id': item['id'], 'hovered': hovered, 'hover_passed': hover_ok, 'camera_preserved': camera_ok})
        check('hover_selection_and_details_signal', selected_ok and signal_ids == [i['id'] for i in items], {'received_once': signal_ids, 'interactions': interactions})
        QTest.qWait(400); geo.grab().save(str(out/'all-satellites-globe.png'))
        geo.set_mode('map'); settle()
        geo.set_view_state((-43.17, -22.95, geo.width()/(MAP_TO_GLOBE_SPAN_M*.99)))
        wheel(-120); to_globe = geo.mode == 'globe'
        wheel(600)
        stays_globe = geo.mode == 'globe'
        for _ in range(4):
            wheel(120)
            stays_globe &= geo.mode == 'globe'
        wheel(120); to_map = geo.mode == 'map'
        span = geo.metres_per_px*geo.width()
        wheel(-120)
        check('scroll_hysteresis_and_focal_region', to_globe and stays_globe and to_map and abs(span-GLOBE_TO_MAP_SPAN_M) < 1 and geo.mode == 'map' and abs(geo.view_state()[0]+43.17) < 1e-8,
              {'entry_span_m': MAP_TO_GLOBE_SPAN_M, 'return_span_m': span,
               'globe_retained_after_nine_scroll_steps': stays_globe})
        controls = geo.controls; focus = geo.focus_node; data = geo.net
        failures = []; max_live = 0
        for index in range(60):
            old = geo.renderer; geo.mode_button.click()
            stopped = not old._timer.isActive() and old.net is None and not old._orbit_paths if isinstance(old, OrbitGlobe) else not old._resize_settle.isActive() and old._paths is None
            cleared = old._base is None
            settle(); stats = geo.resource_stats(); max_live = max(max_live, stats['renderer_instances'])
            if not (stopped and cleared and not shiboken6.isValid(old) and stats['renderer_instances'] == 1 and geo.net is data and geo.renderer.net is data): failures.append(index)
        QTest.qWait(400)
        check('sixty_consecutive_swaps_release_resources', not failures and max_live == 1 and not geo.resource_stats()['transition_raster'],
              {'failed_cycles': failures, 'max_live_renderers': max_live, 'stats': geo.resource_stats()})
        check('shared_inventory_telemetry_filters_and_panels', geo.net is data and geo.net.communicators is data.communicators and geo.controls is controls and geo.focus_node is focus and geo.focus_node.count() == len(net.nodes)+1,
              {'nodes': len(net.nodes), 'links': len(net.links), 'selection': net.selection, 'layer': net.layer, 'route': net.route, 'communicators': len(net.communicators or {})})
        created = geo.resource_stats()['created']
        for _ in range(101): geo.toggle_mode()
        settle()
        check('rapid_swap_requests_coalesce', geo.resource_stats()['created'] == created+1 and geo.resource_stats()['renderer_instances'] == 1, geo.resource_stats())
        geo.hide(); app.processEvents(); paused = geo.globe is None or not geo.globe._timer.isActive()
        geo.show(); app.processEvents()
        check('hidden_globe_animation_pauses', paused and geo.globe is not None and geo.globe._timer.isActive(), 'Only visible active globe animates')
        sizes_ok = True
        for width, height in ((640, 480), (1280, 900), (3440, 1440)):
            geo.resize(width, height); settle(); geo.grab()
            sizes_ok &= all(geo.rect().contains(item['hit'].toRect()) and item['size_px'] >= 32 for item in geo.globe.orbital_items())
            sizes_ok &= geo.controls.geometry().right() < width and geo.mode_button.isVisible()
        check('resolution_and_minimum_icon_size', sizes_ok, '640×480, 1280×900, 3440×1440; icons >=32 logical px, hits >=48')
        rail.resize(60, 650); rail.show(); settle(); names = list(rail.buttons)
        check('communicators_immediately_below_nodes', names[names.index('node')+1] == 'communicators' and bool(rail.buttons['communicators'].toolTip()), names)
        geo.shutdown(); settle()
        check('shutdown_releases_final_renderer', geo.resource_stats()['renderer_instances'] == 0 and geo._fade.image is None, geo.resource_stats())
        report['passed'] = all(checks.values()); report['check_count'] = len(checks)
    except Exception as exc: report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        geo.shutdown(); geo.close(); geo.deleteLater(); rail.close(); rail.deleteLater(); settle()
    (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
