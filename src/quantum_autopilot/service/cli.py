"""Portable OrionQ core: offline provisioning and authenticated headless service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
from threading import Thread

from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.identity import AuthenticationError
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.security.service_identity import (
    ServiceIdentityProvision, read_mounted_service_credential,
)

from .config import load_config
from .clock import HeadlessClock
from .runtime import build_runtime, create_core_server, mounted_provider
from .tls import ServiceTLS, leaf_descriptor, load_leaf


def parser():
    result = argparse.ArgumentParser(prog="orion-core", description=__doc__)
    result.add_argument("--config", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="explicit offline initial provisioning; never during serve")
    provision.add_argument("--server-names", nargs="+", default=[])
    grant = commands.add_parser("grant-certificate", help="offline administrator grant and certificate issuance")
    grant.add_argument("subject")
    grant.add_argument("--roles", nargs="+", required=True)
    grant.add_argument("--scopes", nargs="+", required=True)
    grant.add_argument("--services", nargs="+", required=True)
    grant.add_argument("--kind", choices=("service", "sae", "kme"), default="service")
    grant.add_argument("--output", type=Path, required=True)
    grant.add_argument("--server", action="store_true")
    grant.add_argument("--server-names", nargs="+", default=[])
    revoke = commands.add_parser("revoke-certificate", help="persist revocation for existing TLS connections")
    revoke.add_argument("serial")
    commands.add_parser("status")
    commands.add_parser("check", help="verify identity, vault, audit and provisioned server TLS without listening")
    commands.add_parser("serve")
    return result


def _write_descriptor(path: Path, leaf: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(leaf_descriptor(leaf), stream, indent=2, sort_keys=True)
        stream.write("\n")


def _administration(config, *, initialize=False):
    admin = config.provisioning_identity
    if admin is None or admin.subject == config.identity.subject:
        raise ValueError("offline administration requires a separate provisioning identity")
    credential = read_mounted_service_credential(admin.credential_file)
    options = {"bootstrap": initialize}
    if initialize:
        options["service_bootstrap"] = ServiceIdentityProvision(admin.subject, ("administrator",), ("*",), credential)
    runtime = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
                              secret_provider=mounted_provider(config), **options)
    principal = runtime.authenticate_service(admin.subject, credential)
    return runtime, principal


def _serve(server, *, clock=None, shutdown_timeout_s=10.0):
    old_handlers = {}

    def stop(_signal, _frame):
        # shutdown waits for serve_forever, so it cannot run on that same thread.
        Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, stop)
    try:
        if clock is not None:
            clock.start()
        server.serve_forever(poll_interval=.1)
    finally:
        try:
            # A timeout must not close storage/receivers while a worker or
            # calibration still uses them. Do not claim shutdown succeeded.
            shutdown_deadline = time.monotonic() + shutdown_timeout_s
            begin_draining = getattr(server, "begin_draining", None)
            if begin_draining is not None:
                begin_draining()
            if clock is not None:
                clock.stop(timeout_s=max(.001, shutdown_deadline - time.monotonic()))
            drain_requests = getattr(server, "drain_requests", None)
            if drain_requests is not None:
                drain_requests(timeout_s=max(.001, shutdown_deadline - time.monotonic()))
            server.server_close()
            try:
                engine = server.service._network_engine
                if engine is not None:
                    engine.close_satellite_receivers()
            finally:
                server.service._release_controller_io_resources()
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


def main(argv=None):
    args = parser().parse_args(argv)
    if os.name == "posix":
        os.umask(0o077)
    try:
        config = load_config(args.config)
        if args.command == "provision":
            if (config.security_root / "vault.json").exists() or Path(config.tls["server_leaf"]).exists():
                raise ValueError("initial provisioning cannot replace existing protected state")
            runtime, administrator = _administration(config, initialize=True)
            with runtime.identity_scope(administrator):
                credential = read_mounted_service_credential(config.identity.credential_file)
                scope = "workspace:" + config.state_root.resolve().as_posix()
                runtime.grant_service_identity(config.identity.subject, ("operator",),
                                               (scope, scope + "/*", "certificate:" + config.identity.subject,
                                                "security:identity"), credential)
                leaf = OperationalPKI(config.tls["root"], runtime).provision(config.identity.subject,
                    kind="service", services=["dashboard"], server=True, server_names=args.server_names)
                _write_descriptor(Path(config.tls["server_leaf"]), leaf)
            result = {"status": "provisioned", "identity_profile": "service", "physical_action_performed": False}
        elif args.command in {"grant-certificate", "revoke-certificate"}:
            runtime, administrator = _administration(config)
            with runtime.identity_scope(administrator):
                pki = OperationalPKI(config.tls["root"], runtime)
                if args.command == "grant-certificate":
                    if args.subject in {config.identity.subject, config.provisioning_identity.subject}:
                        raise ValueError("client issuance must not replace core or provisioning identities")
                    if args.output.exists():
                        raise ValueError("certificate descriptor already exists")
                    runtime.grant_identity(args.subject, args.roles, args.scopes, source="certificate")
                    leaf = pki.provision(args.subject, kind=args.kind, services=args.services,
                                         server=args.server, server_names=args.server_names)
                    _write_descriptor(args.output, leaf)
                    result = {"status": "provisioned", "identity": args.subject, "serial": leaf["serial"]}
                else:
                    pki.revoke(args.serial)
                    result = {"status": "revoked", "serial": args.serial}
        else:
            runtime = build_runtime(config)
            if args.command == "status":
                result = runtime.snapshot()
            elif args.command == "check":
                tls = ServiceTLS(config.tls["root"], runtime, load_leaf(config.tls["server_leaf"]),
                                 identity=config.identity.subject)
                tls.server_context()
                result = {"status": "ready", "identity_profile": "service", "vault_mode": runtime.vault.mode,
                          "audit_valid": runtime.audit.verify()["valid"], "tls": "TLSv1.3-mTLS",
                          "physical_action_performed": False}
            else:
                server = create_core_server(config, security=runtime)
                clock_options = config.clock_settings
                clock = None
                if clock_options["enabled"]:
                    clock = HeadlessClock(server.service, period_s=clock_options["period_s"],
                                          stop_timeout_s=clock_options["stop_timeout_s"])
                    server.service._headless_clock = clock
                print(json.dumps({"status": "listening", "port": server.server_port, "tls": "TLSv1.3-mTLS",
                                  "identity_profile": "service", "processing_clock_enabled": clock is not None}), flush=True)
                _serve(server, clock=clock, shutdown_timeout_s=clock_options["stop_timeout_s"])
                return 0
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AuthenticationError, OSError, ValueError, RuntimeError, PermissionError, TypeError, KeyError) as error:
        # No configuration, token, private key, exception text or traceback enters logs.
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
