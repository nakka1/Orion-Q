"""Measure live UI delivery with the real pipeline, also inside the executable."""
from __future__ import annotations

import json
from pathlib import Path
from time import monotonic, perf_counter

from PySide6.QtCore import QPoint, QRectF, QSizeF, QTimer, Qt
from PySide6.QtWidgets import QApplication

from . import theme
from .responsive_validation import run_event_loop
from .service_provider import ServiceProvider
from .shell import ConsoleWindow


def _percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * fraction))] if values else None


def _visible_chart_image(window, chart):
    # Capture the viewport, not chart.grab(): the latter would bypass a stale
    # graphics proxy and falsely certify pixels the operator never sees.
    stage = window.centralWidget()
    origin = chart.mapTo(stage.content, QPoint())
    area = QRectF(origin, QSizeF(chart.size()))
    pixels = stage.mapFromScene(stage.proxy.mapToScene(area)).boundingRect()
    return stage.viewport().grab(pixels).toImage()


def _wait_for_worker_release(provider, *, timeout_ms=5000):
    """Drain asynchronous shutdown while servicing Qt; never accept partial release."""
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 5000:
        raise ValueError("shutdown deadline must be within 1..5000 ms")
    began = perf_counter()
    while provider.stopping and (perf_counter() - began) * 1000 < timeout_ms:
        run_event_loop(5)
    elapsed_ms = (perf_counter() - began) * 1000
    released = (not provider.stopping and provider.shutdown_error is None
                and provider._baseline_worker is None and elapsed_ms <= timeout_ms)
    return bool(released), elapsed_ms


def validate_live_updates(destination, *, repository_root=None, duration_ms=3200, native=False):
    """Five journeys; timings describe this machine, not a hardware real-time guarantee.

    Exclude startup, first map rendering and page transitions from steady-state
    samples. Keep the engine at its production 1 Hz cadence and the real wave
    presentation timer running. A 250 ms gap fails the software smoke gate.
    """
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from .ui_gc import UiThreadCollector

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    previous_theme = theme.current_theme()
    theme.install(app)
    collector = UiThreadCollector(app)
    collector.start()
    service = demo_service(repository_root, destination / 'state')
    for kind in ('qnet', 'qkd'):
        service.network_command('set_model_archive', {'kind': kind, 'path': None})
    provider = ServiceProvider(service, state_root=destination / 'state', replay_limit=3)
    window = ConsoleWindow(provider, skip_auth=True, state_root=destination / 'state')
    from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
    mark_isolated_diagnostic_window(window)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    if native:
        window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    window.resize(1920, 1080)
    window.show()
    errors = []
    provider.error.connect(errors.append)
    heartbeat = QTimer()
    heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
    heartbeat.setInterval(10)
    last, gaps = [None], []
    def beat():
        now = perf_counter()
        if last[0] is not None:
            gaps.append((now - last[0]) * 1000)
        last[0] = now
    heartbeat.timeout.connect(beat)
    report = {'schema': 'orion.live-update-validation.v1', 'passed': False,
              'cases': [], 'errors': errors, 'checks': {}, 'native': native,
              'scope': 'local Qt event loop and real simulated-input pipeline'}
    try:
        window.provider.start()
        deadline = monotonic() + 90
        while window._snap is None or provider.steps_taken < 3 or provider._future is not None:
            if monotonic() >= deadline:
                raise TimeoutError('live pipeline did not finish its startup')
            run_event_loop(20)
        worker = provider._baseline_worker
        worker_pid = worker.process_id
        checks = report['checks']
        checks['local_numerical_process_running'] = worker_pid is not None
        checks['production_tick_period_unchanged'] = provider.step_period_s == 1 and provider._timer.interval() == 1000
        checks['physical_read_only'] = service.health()['physical_read_only'] is True
        checks['optional_archives_off'] = all(service.network_engine.registry.archive_state(kind).state != 'verified'
                                             for kind in ('qnet', 'qkd'))
        topology, telemetry = window.screens['topology'], window.screens['telemetry']
        scenarios = [('topology-one-node', 'topology', 1), ('topology-three-nodes', 'topology', 3),
                     ('fidelity', 'telemetry', 0), ('qber', 'telemetry', 0), ('skr', 'telemetry', 0)]
        for name, page, node_count in scenarios:
            window.show_screen(page)
            if page == 'topology':
                for node in provider.snapshot().topology.nodes[:node_count]:
                    topology._open_window(node.id)
            else:
                telemetry._set_metric(name)
            run_event_loop(600)
            chart = telemetry.chart if page == 'telemetry' else list(topology.node_windows.windows.values())[-1].chart
            before_pixels = _visible_chart_image(window, chart)
            before = provider.snapshot().live.tick
            last[0], gaps[:] = None, []
            heartbeat.start()
            run_event_loop(duration_ms)
            heartbeat.stop()
            pixels_changed = before_pixels != _visible_chart_image(window, chart)
            current = window._snap
            count = current.live.tick - before
            backlog = provider.snapshot().live.tick - current.live.tick
            exact = True
            if page == 'telemetry':
                from .screens.telemetry import history_values
                expected = []
                for slot, link_id in telemetry._slot_links.items():
                    if telemetry._series_on[slot]:
                        xs, ys = history_values(current.live.link(link_id), name)
                        expected.append((link_id, [(float(x), float(y)) for x, y in zip(xs, ys) if y is not None]))
                exact = telemetry.chart.plotted() == expected
            else:
                for node_window in topology.node_windows.windows.values():
                    for series in node_window.chart.series:
                        link = topology._net.link(series.key)
                        exact &= series.samples == tuple(current.live.link(link.id).wave)
            case = {'case': name, 'screen': page, 'ticks': count, 'heartbeat_samples': len(gaps),
                    'delivered_tick': current.live.tick, 'pending_delivery_ticks': backlog,
                    'p50_ms': _percentile(gaps, .5), 'p95_ms': _percentile(gaps, .95),
                    'p99_ms': _percentile(gaps, .99), 'max_ms': max(gaps, default=0),
                    'gaps_over_100_ms': sum(value > 100 for value in gaps),
                    'values_match_backend': bool(exact),
                    'visible_chart_pixels_changed': pixels_changed,
                    'passed': count >= 1 and 0 <= backlog <= 1 and len(gaps) >= 10 and max(gaps, default=0) < 250
                              and bool(exact) and pixels_changed}
            report['cases'].append(case)
            window.grab().save(str(destination / f'{name}.png'))
        checks['every_live_journey_passed'] = all(case['passed'] for case in report['cases'])
        checks['no_provider_errors'] = not errors and provider._step_error is None
        checks['baseline_process_survived_all_views'] = worker.process_id == worker_pid
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        heartbeat.stop()
        window.provider.stop()
        released, shutdown_ms = _wait_for_worker_release(provider)
        report['checks']['worker_released_on_close'] = released
        report['shutdown_deadline_ms'] = 5000
        report['shutdown_wait_ms'] = shutdown_ms
        window.close()
        window.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        collector.stop()
        theme.set_theme(previous_theme)
        theme.install(app)
    report['passed'] = not report.get('error') and all(report['checks'].values())
    (destination / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
