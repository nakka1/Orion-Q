"""Native console entry point.

    python -m quantum_autopilot.console.app                  # in-process service (operator workspace)
    python -m quantum_autopilot.console.app --provider demo  # demonstration: simulated input, real pipeline
    python -m quantum_autopilot.console.app --provider mock  # developer UI fixture (artificial values)
    quantum-twin-console                                     # installed script

The local modes call `AssuranceDashboardService` directly. Remote mode uses an
explicit mutually authenticated TLS connection to a separately deployed core.

Sources. "Local service" is the operator's workspace (no input feed unless one
is configured). "Demonstration" is the SAME service class and network engine on
its own journal (`<state-root>/demo/`), fed by the backend's simulated input
source — the only simulated thing is the input. `--provider synthetic` is a
developer UI stress fixture; it is not offered on the login screen.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from quantum_autopilot.product import PRODUCT_NAME, product_version

from . import theme
from .providers import MockProvider
from .service_provider import ServiceProvider
from .shell import ConsoleWindow


def apply_model_archives(service, args) -> object:
    """Hand the operator's model archives to the engine's model registry.

    The registry verifies each archive itself; an archive that fails verification leaves
    the role on its named baseline and says why in the model status. Without these flags
    the registry still honours ORION_QNET_ARCHIVE / ORION_QKD_ARCHIVE.
    """

    for kind in ("qnet", "qkd"):
        path = getattr(args, f"{kind}_archive", None)
        if path is not None:
            service.network_command("set_model_archive", {"kind": kind, "path": str(Path(path).resolve())})
    return service


def build_service(args) -> object:
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService

    return apply_model_archives(AssuranceDashboardService(
        repository_root=args.repository_root,
        control_journal_path=args.control_journal,
        optical_wdm_csv=args.optical_wdm_csv,
        wdm_model_bundle_path=args.wdm_model_bundle,
        security=console_security(args),
    ), args)


def demo_state_root(args) -> Path:
    from quantum_autopilot.desktop import default_state_root
    return Path(args.state_root or default_state_root())


def console_security(args):
    """One authenticated policy is shared when the operator switches input source."""
    security = getattr(args, "_security", None)
    if security is None:
        from quantum_autopilot.security import SecurityRuntime
        security = SecurityRuntime(demo_state_root(args) / ".security")
        security.local_identity()
        args._security = security
    return security


def build_demo_service(args) -> object:
    """The demonstration service: same class and engine, own journal, simulated INPUT."""
    from quantum_autopilot.desktop import bundled_repository_root
    from quantum_autopilot.qkd.dashboard_api import demo_service

    return apply_model_archives(demo_service(
        args.repository_root or bundled_repository_root(), demo_state_root(args),
        wdm_model_bundle_path=args.wdm_model_bundle, preset=getattr(args, "demo_preset", "brazil-institutions-v1"),
        security=console_security(args)), args)


def build_demo_provider(args):
    return ServiceProvider(build_demo_service(args), poll_ms=args.poll_ms, replay_limit=args.replay_limit,
                           state_root=demo_state_root(args) / "demo")


def build_deferred_provider(args, kind):
    """Prepare services on a worker after the first loading surface is painted."""
    from .prepared_source import DeferredProvider
    security = console_security(args)
    root = demo_state_root(args) / "demo" if kind == "demo" else args.state_root
    prepare = (lambda: build_demo_service(args)) if kind == "demo" else (lambda: build_service(args))
    return DeferredProvider(prepare, lambda service: ServiceProvider(
        service, poll_ms=args.poll_ms, replay_limit=args.replay_limit, state_root=root),
        kind=kind, security=security, state_root=root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quantum-twin-console",
        description="Native operator console for a local workspace or an authenticated remote core.",
    )
    parser.add_argument("--demo-preset", choices=("brazil-institutions-v1", "salvador_metro"),
                        default="brazil-institutions-v1",
                        help="demonstration topology; salvador_metro is the smaller recording workload")
    parser.add_argument("--provider", default="service", choices=("service", "demo", "mock", "synthetic", "measured", "remote"),
                        help="service: operator workspace · demo: simulated input, real pipeline · "
                             "measured: read-only file · remote: mTLS core · synthetic/mock: developer UI fixtures")
    parser.add_argument("--core-url", help="remote mode: HTTPS origin of the independently deployed core")
    parser.add_argument("--core-ca", type=Path, help="trusted CA PEM for the remote core")
    parser.add_argument("--core-cert", type=Path, help="provisioned desktop client certificate PEM")
    parser.add_argument("--core-key", type=Path, help="protected desktop client private-key PEM; never a password argument")
    parser.add_argument("--core-timeout-s", type=float, default=15.0)
    parser.add_argument("--telemetry-file", type=Path, default=None)
    parser.add_argument("--synthetic-profile", default="reference", choices=("reference", "load", "faults"))
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--repository-root", type=Path, default=None)
    parser.add_argument("--control-journal", type=Path, default=None)
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--wdm-model-bundle", type=Path, default=None)
    parser.add_argument("--optical-wdm-csv", type=Path, default=None)
    parser.add_argument("--qnet-archive", type=Path, default=None,
                        help="author's qnet model archive for the engine's model registry")
    parser.add_argument("--qkd-archive", type=Path, default=None,
                        help="author's qkd model archive for the engine's model registry")
    parser.add_argument("--poll-ms", type=int, default=None,
                        help="snapshot cadence; default 1000 with the live network engine")
    parser.add_argument("--replay-limit", type=int, default=40)
    parser.add_argument("--skip-auth", action="store_true",
                        help="skip source selection only; authentication and backend permissions remain enforced")
    parser.add_argument("--fullscreen", action="store_true")
    return parser


def build_provider(args):
    if args.provider == "remote":
        from quantum_autopilot.service.client import CoreClient
        from .remote_provider import RemoteProvider
        from .remote_security import core_tls_context
        if not args.core_url:
            raise ValueError("remote provider requires --core-url")
        if any(getattr(args, name, None) is not None for name in ("control_journal", "telemetry_file", "qnet_archive", "qkd_archive",
                                                                "optical_wdm_csv", "wdm_model_bundle")):
            raise ValueError("remote mode cannot configure local journals, acquisitions or model archives")
        context = core_tls_context(ca_file=args.core_ca, certificate_file=args.core_cert, key_file=args.core_key)
        return RemoteProvider(CoreClient(args.core_url, context, timeout_s=args.core_timeout_s), endpoint=args.core_url,
                              poll_ms=args.poll_ms or 1000)
    console_security(args).authorize("network.read", "workspace:" + demo_state_root(args).resolve().as_posix())
    if args.provider == "measured":
        from .input_sources import MeasuredFileProvider
        if args.telemetry_file is None:
            raise ValueError("measured provider requires --telemetry-file")
        return MeasuredFileProvider(args.telemetry_file, args.state_root)
    if args.provider == "synthetic":
        from quantum_autopilot.desktop import bundled_repository_root, default_state_root
        from uuid import uuid4
        from .synthetic import SyntheticProvider
        return SyntheticProvider(args.repository_root or bundled_repository_root(),
            args.state_root or default_state_root() / "synthetic" / uuid4().hex,
            profile=args.synthetic_profile, seed=args.seed)
    if args.provider == "mock":
        return MockProvider()
    if args.provider == "demo":
        return build_deferred_provider(args, "demo")
    return build_deferred_provider(args, "service")


def run(args, *, startup_monitor=None, desktop_instance=None, isolated_diagnostic=False) -> int:
    """Acquire one operator workspace before constructing services or windows."""
    from quantum_autopilot.desktop_instance import acquire_desktop_instance, report_instance_error

    diagnostic = isolated_diagnostic or startup_monitor is not None
    if diagnostic:
        return _run_workspace(args, startup_monitor=startup_monitor, isolated_diagnostic=True)
    try:
        instance = desktop_instance or acquire_desktop_instance()
    except OSError as error:
        report_instance_error(error)
        return 2
    owned = desktop_instance is None
    try:
        if not instance.is_primary:
            if not instance.request_activation() and instance.activation_error is not None:
                report_instance_error(instance.activation_error)
            return 0
        return _run_workspace(args, startup_monitor=startup_monitor, desktop_instance=instance)
    finally:
        if owned:
            instance.close()


def _run_workspace(args, *, startup_monitor=None, desktop_instance=None, isolated_diagnostic=False) -> int:
    from .display import configure_high_dpi, show_main_window
    from .branding import app_icon, configure_windows_app_identity
    configure_windows_app_identity()
    configure_high_dpi()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(PRODUCT_NAME)
    app.setApplicationVersion(product_version())
    # Keep the existing Qt settings namespace during the public rename.
    app.setOrganizationName("ORION-Q")
    app.setWindowIcon(app_icon())
    # Protect the complete widget lifecycle, including construction. Starting
    # this only after show() leaves its first wrappers to automatic collection.
    from .ui_gc import UiThreadCollector
    collector = UiThreadCollector(app)
    collector.start()
    window = None
    try:
        theme.install(app)
        provider = build_provider(args)
        def service_factory():
            return build_deferred_provider(args, "service")
        if args.provider == "remote":
            from .remote_security import RemoteConsoleWindow
            window = RemoteConsoleWindow(provider, skip_auth=args.skip_auth, state_root=args.state_root)
        else:
            window = ConsoleWindow(provider, skip_auth=args.skip_auth, service_factory=service_factory,
                                   state_root=args.state_root, demo_factory=lambda: build_deferred_provider(args, "demo"),
                                   security=console_security(args))
        if isolated_diagnostic:
            from quantum_autopilot.desktop_instance import mark_isolated_diagnostic_window
            mark_isolated_diagnostic_window(window)
        elif desktop_instance is not None:
            desktop_instance.attach_qt_window(window)
        def start_source_after_paint():
            # The first QGraphicsProxy layout/polish enters many Python Qt
            # callbacks. Starting acquisition concurrently makes that cold
            # frame wait on the worker's GIL. Authentication and construction
            # have already finished; only acquisition waits for the painted
            # surface. Queue it after paintEvent returns, without a fixed delay.
            def start_visible_source():
                if window.isVisible():
                    window.provider.start()
            QTimer.singleShot(0, window, start_visible_source)
        window.centralWidget().first_frame_painted.connect(start_source_after_paint)
        show_main_window(window, fullscreen=args.fullscreen)
        if startup_monitor is not None:
            startup_monitor.attach(app, window)
        return app.exec()
    finally:
        try:
            if window is not None:
                window.provider.stop()
        finally:
            collector.stop()


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
