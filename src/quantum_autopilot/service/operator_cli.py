"""Authenticated terminal operator for an independently running OrionQ core.

This client does not create a workspace, security runtime, engine or GUI. It
uses a provisioned client certificate and performs exactly one HTTP request.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys

from quantum_autopilot.security.http_boundary import strict_object

from .client import CoreClient, CoreRequestError


MAX_INPUT_BYTES = 1024 * 1024
EXIT_INPUT = 2
EXIT_REQUEST = 3
EXIT_UNCERTAIN = 4
EXIT_OUTPUT = 5


class OperatorUsageError(ValueError):
    """Invalid command arguments; never include their raw contents in logs."""


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise OperatorUsageError("invalid operator arguments; use --help")


def parser():
    result = _Parser(prog="orion-operator", description=__doc__)
    result.add_argument("--url", "--core-url", dest="url", required=True)
    result.add_argument("--ca", "--core-ca", dest="ca", type=Path, required=True)
    result.add_argument("--cert", "--core-cert", dest="cert", type=Path, required=True)
    result.add_argument("--key", "--core-key", dest="key", type=Path, required=True)
    result.add_argument("--timeout-s", "--core-timeout-s", dest="timeout_s", type=float, default=30.0)
    result.add_argument("--output", type=Path, help="exclusive JSON output file; otherwise stdout")
    commands = result.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("health", "topology", "mixed-status"):
        commands.add_parser(name)
    for name in ("state", "step"):
        command = commands.add_parser(name)
        command.add_argument("--metadata-only", action="store_true")
    for name in ("network-command", "console-command"):
        command = commands.add_parser(name)
        command.add_argument("verb")
        command.add_argument("--input", required=True, help="JSON file or '-' for bounded stdin")
    evidence = commands.add_parser("evidence", help="authorized backend evidence, with no server output path")
    evidence.add_argument("--scope", required=True)
    for name in ("mixed-configure", "mixed-ingest", "mixed-demand", "mixed-release"):
        command = commands.add_parser(name)
        command.add_argument("--input", required=True, help="JSON file or '-' for bounded stdin")
    watch = commands.add_parser("watch", help="read-only live terminal; finite --frames required without TTY")
    watch.add_argument("--interval-s", type=float, default=1.)
    watch.add_argument("--frames", type=int)
    watch.add_argument("--view", choices=("overview", "links", "nodes", "satellites", "reservations", "audit", "raw", "mixed"), default="overview")
    watch.add_argument("--select")
    watch.add_argument("--layout", choices=("auto", "dashboard", "plain"), default="auto",
                       help="auto: dashboard in a Linux TTY, plain for finite reports; v toggles in TTY")
    return result


def build_tls_context(ca: Path, cert: Path, key: Path, *, key_password_reader=None):
    """TLS 1.3 and explicit credential acquisition; never an OpenSSL prompt.

    Institutional Python callers may supply a protected reader. The terminal
    command has no password/token arguments and never opens a core vault.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_verify_locations(cafile=str(ca))

    def password():
        if key_password_reader is None:
            return b""
        try:
            value = key_password_reader()
            if not isinstance(value, bytes) or not 1 <= len(value) <= 4096:
                raise ValueError("invalid protected key credential")
            return value
        except Exception:
            raise ValueError("protected key credential unavailable") from None

    context.load_cert_chain(certfile=str(cert), keyfile=str(key), password=password)
    return context


def read_payload(source):
    """Bound input before decoding and reject duplicates/nonfinite/deep JSON."""
    if source == "-":
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        data = stream.read(MAX_INPUT_BYTES + 1)
        if isinstance(data, str):
            data = data.encode("utf-8")
    else:
        path = Path(source)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_INPUT_BYTES:
            raise ValueError("bounded regular JSON input file required")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or not 0 < opened.st_size <= MAX_INPUT_BYTES
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError("JSON input changed before admission")
            data = stream.read(MAX_INPUT_BYTES + 1)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_INPUT_BYTES:
        raise ValueError("bounded JSON input required")
    return strict_object(data)


def execute(client, args, payload=None):
    """One explicit operation; protocol/device compatibility is backend policy."""
    command = args.command
    if command == "health":
        return client.health()
    if command == "topology":
        return client.topology()
    if command == "state":
        return client.network_state(metadata_only=args.metadata_only)
    if command == "step":
        return client.network_step(metadata_only=args.metadata_only)
    if command == "network-command":
        return client.network_command(args.verb, payload)
    if command == "console-command":
        return client.console_command(args.verb, payload)
    if command == "evidence":
        return client.console_evidence(args.scope)
    if command == "mixed-status":
        return client.mixed_status()
    methods = {"mixed-configure": "mixed_configure", "mixed-ingest": "mixed_ingest",
               "mixed-demand": "mixed_demand", "mixed-release": "mixed_release"}
    return getattr(client, methods[command])(payload)


def _write_result(result, destination):
    encoded = json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    if destination is None:
        sys.stdout.write(encoded)
        sys.stdout.flush()
        return
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def _error(status, *, error_type, http_status=None, guidance=None):
    value = {"status": status, "error_type": error_type, "automatic_retry": False}
    if http_status is not None:
        value["http_status"] = http_status
    if guidance is not None:
        value["guidance"] = guidance
    print(json.dumps(value, sort_keys=True), file=sys.stderr)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if hasattr(args, "verb") and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", args.verb) is None:
            raise ValueError("bounded service operation required")
        if hasattr(args, "scope") and (not args.scope.strip() or len(args.scope) > 128
                                       or any(ord(character) < 32 for character in args.scope)):
            raise ValueError("bounded evidence scope required")
        payload = read_payload(args.input) if hasattr(args, "input") else None
        context = build_tls_context(args.ca, args.cert, args.key)
        client = CoreClient(args.url, context, timeout_s=args.timeout_s)
    except (OSError, ValueError, RuntimeError, TypeError, RecursionError) as error:
        _error("invalid_input_or_tls_identity", error_type=type(error).__name__,
               guidance="Use --help and externally provisioned TLS credentials; no interactive password prompt is used.")
        return EXIT_INPUT
    if args.command == "watch":
        try:
            from .terminal_live import watch
            if args.output is None:
                return watch(client, interval_s=args.interval_s, frames=args.frames,
                             view=args.view, selected=args.select, layout=args.layout)
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                return watch(client, interval_s=args.interval_s, frames=args.frames,
                             view=args.view, selected=args.select, stream=stream, layout=args.layout)
        except (ValueError, TypeError) as error:
            _error("invalid_terminal_configuration", error_type=type(error).__name__)
            return EXIT_INPUT
        except OSError as error:
            _error("terminal_output_unavailable", error_type=type(error).__name__,
                   guidance="No mutation was issued by watch. Use a new accessible output or terminal.")
            return EXIT_OUTPUT
        except RuntimeError as error:
            _error("terminal_unavailable", error_type=type(error).__name__)
            return EXIT_REQUEST
    try:
        result = execute(client, args, payload)
    except CoreRequestError as error:
        if error.uncertain:
            _error("mutation_outcome_uncertain", error_type="CoreRequestError", http_status=error.status,
                   guidance="Do not resend automatically. Inspect state and durable receipts using read-only requests.")
            return EXIT_UNCERTAIN
        _error("request_rejected_or_unavailable", error_type="CoreRequestError", http_status=error.status)
        return EXIT_REQUEST
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        _error("request_failed", error_type=type(error).__name__,
               guidance="No retry was attempted. Inspect backend state before repeating a mutation.")
        return EXIT_REQUEST
    try:
        _write_result(result, args.output)
    except (OSError, ValueError, TypeError) as error:
        _error("response_received_output_unavailable", error_type=type(error).__name__,
               guidance="The backend response was received. Do not repeat the mutation merely to recreate an output file; inspect backend records.")
        return EXIT_OUTPUT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
