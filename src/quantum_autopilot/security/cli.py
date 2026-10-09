"""Explicit local administration. Credentials are never printed or logged."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import SecurityRuntime
from .identity import OIDCConfig


def configured_runtime(root, *, profile="local", oidc_configuration=None):
    oidc = None
    if oidc_configuration is not None:
        value = json.loads(Path(oidc_configuration).read_text(encoding="utf-8"))
        if set(value) - {"issuer", "audience", "jwks", "algorithms", "clock_skew_seconds",
                         "max_token_lifetime_seconds"}:
            raise ValueError("unknown OIDC configuration field")
        oidc = OIDCConfig(**value)
    return SecurityRuntime(root, profile=profile, oidc=oidc)


def parser():
    result = argparse.ArgumentParser(prog="orion-q-security", description=__doc__)
    result.add_argument("--root", type=Path, required=True, help="protected deployment security directory")
    result.add_argument("--profile", choices=("local", "operational"), default="local")
    result.add_argument("--oidc-configuration", type=Path)
    result.add_argument("--report", type=Path, help="write the non-secret administration result")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("verify-audit")
    grant = commands.add_parser("grant")
    grant.add_argument("subject")
    grant.add_argument("--roles", nargs="+", required=True)
    grant.add_argument("--scopes", nargs="+", required=True)
    grant.add_argument("--source", choices=("os", "service", "oidc", "certificate"), default="service")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("subject")
    rotate = commands.add_parser("rotate-secret")
    rotate.add_argument("name")
    serve = commands.add_parser("serve-local")
    serve.add_argument("--journal", type=Path, required=True)
    serve.add_argument("--repository-root", type=Path)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--session-reference", default="http-local-session")
    provision = commands.add_parser("provision-certificate")
    provision.add_argument("identity")
    provision.add_argument("--certificates", type=Path, required=True)
    provision.add_argument("--kind", choices=("sae", "kme", "service"), required=True)
    provision.add_argument("--services", nargs="+", required=True)
    provision.add_argument("--server", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    runtime = configured_runtime(args.root, profile=args.profile, oidc_configuration=args.oidc_configuration)
    if args.command == "status":
        result = runtime.snapshot()
    elif args.command == "verify-audit":
        runtime.authorize("security.read", "security:audit")
        result = runtime.audit.verify()
    elif args.command == "grant":
        identity = runtime.grant_identity(args.subject, args.roles, args.scopes, source=args.source)
        result = {"subject": identity.subject, "roles": sorted(identity.roles), "scopes": list(identity.scopes)}
    elif args.command == "revoke":
        runtime.revoke_identity(args.subject)
        result = {"revoked": True}
    elif args.command == "rotate-secret":
        result = {"reference": runtime.rotate_secret(args.name)}
    elif args.command == "provision-certificate":
        from .pki import OperationalPKI
        leaf = OperationalPKI(args.certificates, runtime).provision(args.identity, kind=args.kind,
                    services=args.services, server=args.server)
        result = {key: str(value) for key, value in leaf.items() if key != "policy"}
    else:
        from quantum_autopilot.qkd.dashboard_api import create_server
        server = create_server(args.repository_root, port=args.port, control_journal_path=args.journal, security=runtime)
        # Local authenticated clients under this OS account can resolve this
        # encrypted vault reference. No unauthenticated token-issuance route.
        runtime.vault.put_bytes(args.session_reference, runtime.issue_local_session().encode())
        result = {"address": "127.0.0.1", "port": server.server_port,
                  "session_reference": args.session_reference}
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
        if __import__("sys").stdout is not None:
            print(json.dumps(result, sort_keys=True), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    if __import__("sys").stdout is not None:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
