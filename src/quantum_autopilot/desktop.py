"""Windows desktop entry point for the local quantum-network control sandbox.

Two user interfaces share one application service:

* ``--ui native`` (default) opens the PySide6 operator console in-process. No
  socket is opened and no browser is involved.
* ``--ui web`` serves the static dashboard and control API on one loopback
  origin, exactly as before.

Neither binds to the LAN and neither enables physical actuation.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from threading import Timer

from quantum_autopilot.qkd.dashboard_api import DEFAULT_HOST, DEFAULT_PORT, create_server
from quantum_autopilot.product import PRODUCT_NAME, product_version


DEFAULT_MODEL_BUNDLE = Path(
    "outputs/wdm_model_bundle_v527/edge_lstm_dual_head_uniform_v1.wdmbundle.npz"
)


@dataclass(frozen=True)
class DesktopPaths:
    """Resolved read-only bundle resources and writable application state."""

    repository_root: Path
    dashboard_root: Path
    state_root: Path
    control_journal: Path
    wdm_model_bundle: Path | None


@dataclass(frozen=True)
class NativePaths:
    """Resources for the native console; no built web UI is required."""

    repository_root: Path
    state_root: Path
    control_journal: Path
    wdm_model_bundle: Path | None


def bundled_repository_root() -> Path:
    """Return the PyInstaller extraction root or the source checkout root."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[2]


def default_state_root() -> Path:
    """Keep mutable runtime records outside the executable bundle."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return (base / "ORION-Q").resolve()


def _resolve_model_bundle(repository: Path, wdm_model_bundle: str | Path | None) -> Path | None:
    candidate = Path(wdm_model_bundle or repository / DEFAULT_MODEL_BUNDLE).resolve()
    if wdm_model_bundle is not None and not candidate.is_file():
        raise ValueError("wdm_model_bundle must be an existing file")
    return candidate if candidate.is_file() else None


def resolve_desktop_paths(
    *,
    repository_root: str | Path | None = None,
    dashboard_root: str | Path | None = None,
    state_root: str | Path | None = None,
    wdm_model_bundle: str | Path | None = None,
) -> DesktopPaths:
    """Resolve and validate all resources before a local server is opened."""

    repository = Path(repository_root or bundled_repository_root()).resolve()
    dashboard = Path(
        dashboard_root or repository / "dashboard" / "dist" / "client"
    ).resolve()
    state = Path(state_root or default_state_root()).resolve()
    if not repository.is_dir():
        raise ValueError("repository_root must be an existing directory")
    if not (dashboard / "index.html").is_file():
        raise ValueError(
            "dashboard_root must contain index.html; run tools/build_windows_exe.ps1 first"
        )
    state.mkdir(parents=True, exist_ok=True)
    return DesktopPaths(
        repository_root=repository,
        dashboard_root=dashboard,
        state_root=state,
        control_journal=state / "sandbox_journal.sqlite",
        wdm_model_bundle=_resolve_model_bundle(repository, wdm_model_bundle),
    )


def resolve_native_paths(
    *,
    repository_root: str | Path | None = None,
    state_root: str | Path | None = None,
    wdm_model_bundle: str | Path | None = None,
) -> NativePaths:
    """Resolve resources for the native console; the built web UI is not needed."""

    repository = Path(repository_root or bundled_repository_root()).resolve()
    state = Path(state_root or default_state_root()).resolve()
    if not repository.is_dir():
        raise ValueError("repository_root must be an existing directory")
    state.mkdir(parents=True, exist_ok=True)
    return NativePaths(
        repository_root=repository,
        state_root=state,
        control_journal=state / "sandbox_journal.sqlite",
        wdm_model_bundle=_resolve_model_bundle(repository, wdm_model_bundle),
    )


def find_edge_executable() -> Path | None:
    """Locate Microsoft Edge for an app-like window without bundling a webview."""

    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            candidates.append(
                Path(value) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def open_dashboard_window(url: str, state_root: Path) -> None:
    """Open an Edge application window, falling back to the default browser."""

    edge = find_edge_executable()
    if edge is None:
        webbrowser.open_new(url)
        return
    profile = state_root / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(  # noqa: S603 - executable path is locally resolved and fixed
        [
            str(edge),
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-extensions",
        ],
        close_fds=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Launch the local {PRODUCT_NAME} desktop control sandbox."
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT_NAME} {product_version()}")
    parser.add_argument(
        "--ui",
        default="native",
        choices=("native", "web"),
        help="native PySide6 console (default) or the loopback web dashboard",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--repository-root", type=Path, default=None)
    parser.add_argument("--dashboard-root", type=Path, default=None)
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--wdm-model-bundle", type=Path, default=None)
    parser.add_argument("--demo", action="store_true",
                        help="start directly in demonstration mode: simulated input, real processing pipeline")
    parser.add_argument("--qnet-archive", type=Path, default=None,
                        help="author's qnet model archive (routing, compensation, allocation models)")
    parser.add_argument("--qkd-archive", type=Path, default=None,
                        help="author's qkd model archive (synchronisation, key-rate models)")
    parser.add_argument("--synthetic", choices=("reference", "load", "faults"), default=None,
                        help="developer UI stress fixture (artificial display values); not the demonstration")
    parser.add_argument("--telemetry-file", type=Path, default=None, help="read-only measured telemetry JSON input")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--stress-test", type=Path, default=None, metavar="OUTPUT_DIR")
    parser.add_argument("--stress-rounds", type=int, default=20)
    parser.add_argument("--self-test", type=Path, default=None, metavar="REPORT_JSON",
                        help="validate the native distribution using an isolated state directory")
    parser.add_argument('--satellite-test',type=Path,default=None,metavar='OUTPUT_DIR',
                        help='validate satellite/OGS v2 pipeline in an isolated directory; no hardware')
    parser.add_argument('--global-network-test',type=Path,default=None,metavar='OUTPUT_DIR',
                        help='validate global demo demands, GEO/ISL, failures, recovery and matrices in isolated journals')
    parser.add_argument('--lab-test',type=Path,default=None,metavar='OUTPUT_DIR',
                        help='validate laboratory acquisition, independent KMEs, uncertain delivery and registered replay; no hardware')
    parser.add_argument("--startup-test", type=Path, default=None, metavar="REPORT_JSON",
                        help="validate ordinary native startup and layout controls on an isolated journal")
    parser.add_argument("--startup-size", default=None, metavar="WIDTHxHEIGHT",
                        help="explicit logical client size for --startup-test (default: actual monitor geometry)")
    parser.add_argument("--performance-test", type=Path, default=None, metavar="OUTPUT_DIR",
                        help="measure the authenticated demo console in fresh state; no physical acquisition")
    parser.add_argument("--performance-duration", type=float, default=20,
                        help="seconds per map/globe/tabs/swaps benchmark phase")
    parser.add_argument("--performance-soak", type=float, default=3,
                        help="additional benchmark continuity phase in minutes")
    parser.add_argument("--performance-size", default="3440x1440", metavar="WIDTHxHEIGHT")
    parser.add_argument("--performance-profile", action="store_true",
                        help="separate diagnostic pass with method timings and cProfile")
    parser.add_argument("--performance-local-profile", action="store_true",
                        help="separate show/layout/GC spans without global Qt event interception")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="serve the desktop UI without opening a window (--ui web only)",
    )
    return parser


def _run_native(args, *, startup_monitor=None, desktop_instance=None) -> int:
    from quantum_autopilot.console.app import build_parser as console_parser, run

    if args.synthetic and args.state_root is None:
        from uuid import uuid4
        args.state_root = default_state_root() / "synthetic" / uuid4().hex
    try:
        paths = resolve_native_paths(
            repository_root=args.repository_root,
            state_root=args.state_root,
            wdm_model_bundle=args.wdm_model_bundle,
        )
    except (OSError, ValueError) as exc:
        print(f"{PRODUCT_NAME}: initialization failed: {exc}", file=sys.stderr)
        return 2

    print(f"{PRODUCT_NAME} console: native window", flush=True)
    print(
        "Control scope: local simulation/shadow only; physical actuation is locked.",
        flush=True,
    )
    print(f"Runtime journal: {paths.control_journal}", flush=True)

    console_args = console_parser().parse_args([])
    console_args.repository_root = paths.repository_root
    console_args.control_journal = paths.control_journal
    console_args.state_root = paths.state_root
    console_args.wdm_model_bundle = paths.wdm_model_bundle
    console_args.qnet_archive = args.qnet_archive
    console_args.qkd_archive = args.qkd_archive
    if args.demo:
        console_args.provider = "demo"
    if args.synthetic:
        console_args.provider = "synthetic"
        console_args.synthetic_profile = args.synthetic
        console_args.seed = args.seed
    if args.telemetry_file:
        console_args.provider = "measured"
        console_args.telemetry_file = args.telemetry_file
    options = {}
    if startup_monitor is not None:
        options["startup_monitor"] = startup_monitor
    if desktop_instance is not None:
        options["desktop_instance"] = desktop_instance
    return run(console_args, **options)


def _run_web(args, *, desktop_instance=None) -> int:
    try:
        paths = resolve_desktop_paths(
            repository_root=args.repository_root,
            dashboard_root=args.dashboard_root,
            state_root=args.state_root,
            wdm_model_bundle=args.wdm_model_bundle,
        )
        server = create_server(
            repository_root=paths.repository_root,
            host=args.host,
            port=args.port,
            control_journal_path=paths.control_journal,
            wdm_model_bundle_path=paths.wdm_model_bundle,
            dashboard_root=paths.dashboard_root,
            security_root=paths.state_root / ".security",
        )
    except (OSError, ValueError) as exc:
        print(f"{PRODUCT_NAME}: initialization failed: {exc}", file=sys.stderr)
        return 2

    url = f"http://{args.host}:{server.server_port}/"
    print(f"{PRODUCT_NAME} desktop: {url}", flush=True)
    print(
        "Control scope: local simulation/shadow only; physical actuation is locked.",
        flush=True,
    )
    print(f"Runtime journal: {paths.control_journal}", flush=True)
    browser_timer = None
    try:
        if not args.no_browser:
            session = server.service.security.issue_local_session()
            if desktop_instance is not None:
                # Focus an existing browser window on repeat launch; issuing another
                # local session or opening another app window is unnecessary.
                desktop_instance.attach_web_dashboard(paths.dashboard_root / "index.html")
            # Only the fragment contains the session. It is removed by the client
            # and neither sent in the HTTP target nor printed to the launch log.
            browser_timer = Timer(0.4, open_dashboard_window,
                                  args=(url + "#orion_session=" + session, paths.state_root))
            browser_timer.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    security_arguments = list(sys.argv[1:] if argv is None else argv)
    if security_arguments and security_arguments[0] == "--branding-test":
        if len(security_arguments) != 2:
            raise ValueError("--branding-test requires an isolated output directory")
        from quantum_autopilot.console.branding_validation import run as run_branding_test
        return run_branding_test(security_arguments[1])
    if security_arguments and security_arguments[0] == "--desktop-behavior-test":
        from quantum_autopilot.console.desktop_validation import main as desktop_test_main
        return desktop_test_main(security_arguments[1:])
    if security_arguments and security_arguments[0] == "--security-admin":
        from quantum_autopilot.security.cli import main as security_main
        return security_main(security_arguments[1:])
    if security_arguments and security_arguments[0] == "--ui-pipeline-test":
        if len(security_arguments) != 2:
            raise ValueError("--ui-pipeline-test requires a new isolated output directory")
        from quantum_autopilot.console.ui_pipeline_validation import validate_ui_pipeline
        report = validate_ui_pipeline(security_arguments[1], bundled_repository_root())
        return 0 if report["passed"] else 1
    if security_arguments and security_arguments[0] == '--satellite-gateway':
        from quantum_autopilot.quantum_protocols.satellite_gateway_cli import main as gateway_main
        return gateway_main(security_arguments[1:])
    if security_arguments and security_arguments[0] == '--satellite-mission-test':
        if len(security_arguments) != 2:
            raise ValueError('--satellite-mission-test requires an isolated output directory')
        from quantum_autopilot.quantum_protocols.mission_validation import validate_mission_reception
        return 0 if validate_mission_reception(security_arguments[1])['passed'] else 1
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.performance_test:
        if (args.ui != "native" or args.state_root or args.wdm_model_bundle or args.qnet_archive
                or args.qkd_archive or any((args.self_test, args.satellite_test,
                    args.global_network_test, args.lab_test, args.startup_test, args.stress_test,
                    args.synthetic, args.telemetry_file, args.demo))):
            parser.error('--performance-test requires isolated native demo input and cannot use operator state or models')
        from quantum_autopilot.console.performance_validation import performance_options, run_performance_test
        try:
            performance_options(args.performance_test, duration=args.performance_duration,
                soak=args.performance_soak, size=args.performance_size,
                profile=args.performance_profile, local_profile=args.performance_local_profile,
                repository_root=args.repository_root)
            return run_performance_test(args)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    elif (args.performance_profile or args.performance_local_profile or args.performance_duration != 20
            or args.performance_soak != 3 or args.performance_size != "3440x1440"):
        parser.error('--performance-* options require --performance-test')
    if args.startup_size and not args.startup_test:
        parser.error('--startup-size requires --startup-test')
    if args.startup_test and (args.self_test or args.satellite_test or args.global_network_test or args.lab_test or args.stress_test or args.synthetic or args.telemetry_file or args.ui != 'native'):
        parser.error('--startup-test is native-only and cannot be combined with other diagnostic inputs')
    if sum(bool(v) for v in (args.self_test, args.satellite_test, args.global_network_test, args.lab_test, args.stress_test, args.synthetic, args.telemetry_file, args.demo)) > 1:
        parser.error("choose only one of --self-test, --stress-test, --synthetic, --telemetry-file or --demo")
    if args.ui != "native" and (args.synthetic or args.satellite_test or args.global_network_test or args.lab_test or args.stress_test or args.telemetry_file or args.demo):
        parser.error("--demo, --synthetic, --telemetry-file and --stress-test are native/local only")
    for name in ("qnet_archive", "qkd_archive"):
        archive = getattr(args, name)
        if archive is not None and not archive.is_file():
            parser.error(f"--{name.replace('_', '-')} must be an existing file")
        if archive is not None and args.ui != "native":
            parser.error("model archives are used by the native console's network engine only")
    if args.stress_test:
        from quantum_autopilot.console.stress import run_controller_stress
        report = run_controller_stress(args.repository_root or bundled_repository_root(), args.stress_test,
                                       seed=args.seed, rounds=args.stress_rounds)
        print(report["report_path"])
        return 0 if report["passed"] else 1
    if args.satellite_test:
        from .quantum_protocols.satellite_validation import validate_satellite_pipeline
        report=validate_satellite_pipeline(args.satellite_test,args.seed)
        return 0 if report['passed'] else 1
    if args.global_network_test:
        from .network_engine.global_validation import validate_global_network
        report=validate_global_network(args.global_network_test,args.repository_root or bundled_repository_root())
        return 0 if report['passed'] else 1
    if args.lab_test:
        from .lab.validation import validate_laboratory
        return 0 if validate_laboratory(args.lab_test)["passed"] else 1
    if args.startup_test:
        from quantum_autopilot.console.startup_validation import run_startup_test
        return run_startup_test(args)
    if args.self_test:
        from quantum_autopilot.console.validation import run_self_test
        # Diagnostics must never default to the operator's persistent journal.
        from tempfile import mkdtemp
        diagnostic_state = args.state_root
        if diagnostic_state is None:
            args.self_test.resolve().parent.mkdir(parents=True, exist_ok=True)
            diagnostic_state = Path(mkdtemp(prefix="self-test-state-", dir=args.self_test.resolve().parent))
        paths = resolve_native_paths(
            repository_root=args.repository_root, state_root=diagnostic_state,
            wdm_model_bundle=args.wdm_model_bundle)
        return run_self_test(paths, args.self_test)
    if args.ui == "web" and args.no_browser:
        return _run_web(args)
    from quantum_autopilot.desktop_instance import acquire_desktop_instance, report_instance_error
    try:
        instance = acquire_desktop_instance()
    except OSError as exc:
        report_instance_error(exc)
        return 2
    with instance:
        if not instance.is_primary:
            activated = instance.request_activation()
            print(f"{PRODUCT_NAME}: already running on this machine; activation requested.", flush=True)
            if not activated and instance.activation_error is not None:
                report_instance_error(instance.activation_error)
            return 0
        if args.ui == "native":
            return _run_native(args, desktop_instance=instance)
        return _run_web(args, desktop_instance=instance)


if __name__ == "__main__":
    raise SystemExit(main())
