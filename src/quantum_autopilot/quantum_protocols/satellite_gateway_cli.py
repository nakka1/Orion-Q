"""Explicit mission gateway: archived/public frames and protected local ingress."""

import argparse
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

from .satellite_gateway import GatewayOutbox, MissionGatewayClient, PublicFrameClient, radio_envelope
from .satellite_missions import SATNOGS_BEACON0_PROFILE, CSP1_BEACON0_PROFILE
from ..security import SecurityRuntime
from ..security.http_boundary import strict_object


@contextmanager
def capture_snapshot(path, *, directory, max_bytes, timeout_s):
    """Hash the exact bounded snapshot that will be parsed, including on Python 3.10."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= 256 * 1024 * 1024:
        raise ValueError("capture byte quota must be 1..256 MiB")
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 3600:
        raise ValueError("capture deadline must be 0..3600 seconds")
    snapshot = None
    try:
        digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + timeout_s
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix="satellite-capture-", suffix=".kiss", delete=False
        ) as target:
            snapshot = Path(target.name)
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    count += len(chunk)
                    if count > max_bytes or time.monotonic() > deadline:
                        raise ValueError("capture exceeds explicit byte/time quota")
                    digest.update(chunk)
                    target.write(chunk)
        yield snapshot, digest.hexdigest()
    finally:
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)


def parser():
    result = argparse.ArgumentParser(prog="orion-q-satellite-gateway", description=__doc__)
    result.add_argument("--outbox", type=Path)
    result.add_argument("--source-id", default="mission_gateway")
    result.add_argument(
        "--origin", choices=("physical", "measurement_file", "simulated"), default="measurement_file"
    )
    result.add_argument("--satellite-id", default="SUOMI100")
    result.add_argument(
        "--profile",
        help="explicit mission frame profile; KISS defaults to CSP1, public files to observed SatNOGS",
    )
    result.add_argument("--report", type=Path)
    actions = result.add_subparsers(dest="action", required=True)
    archive = actions.add_parser(
        "import-jsonl", help="enqueue original frame envelopes; never alter reception clocks"
    )
    archive.add_argument("path", type=Path)
    public = actions.add_parser("satnogs", help="bounded explicit public acquisition; no third-party uploads")
    public.add_argument("--observation", type=int, required=True)
    public.add_argument("--norad", type=int, default=43804)
    public.add_argument("--timeout", type=float, default=5.0)
    public.add_argument("--polls", type=int, default=1)
    public.add_argument("--interval", type=float, default=60.0)
    for kind in ("kiss-file", "kiss-tcp"):
        radio = actions.add_parser(kind, help="bounded read-only input from an external demodulator")
        if kind == "kiss-file":
            radio.add_argument("path", type=Path)
            radio.add_argument(
                "--captured-at",
                required=True,
                help="explicit UTC capture clock; KISS timestamps remain distinct",
            )
        else:
            radio.add_argument("--host", default="127.0.0.1")
            radio.add_argument("--port", type=int, default=8100)
            radio.add_argument(
                "--capture-id", required=True, help="new unique identifier for this acquisition session"
            )
        radio.add_argument("--station-id", required=True)
        radio.add_argument("--station-name", required=True)
        radio.add_argument("--mission-id", default="suomi100")
        radio.add_argument("--norad", type=int, default=43804)
        radio.add_argument("--max-frames", type=int, default=1000)
        radio.add_argument("--timeout", type=float, default=30.0)
        radio.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    deliver = actions.add_parser("deliver")
    deliver.add_argument("--security-root", type=Path, required=True)
    deliver.add_argument("--credential-ref", default="vault:orion-mission-session")
    deliver.add_argument("--url", default="ws://127.0.0.1:8766/satellite/v1/frames")
    deliver.add_argument(
        "--follow-seconds",
        type=float,
        default=0.0,
        help="keep draining the durable queue in a separate process, up to 3600 s",
    )
    deliver.add_argument("--batch-size", type=int, default=32)
    deliver.add_argument("--interval", type=float, default=1.0)
    serve = actions.add_parser("serve")
    serve.add_argument("--state-root", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--duration", type=float, default=3600.0)
    serve.add_argument("--mission-id", default="suomi100")
    serve.add_argument("--norad", type=int, default=43804)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    profile = args.profile or (
        CSP1_BEACON0_PROFILE if args.action.startswith("kiss-") else SATNOGS_BEACON0_PROFILE
    )
    if args.action == "serve":
        from .mission_ingress import MissionFrameStore, MissionWebSocketServer

        if not 0 < args.duration <= 86400:
            raise ValueError("receiver duration must be 0..86400 seconds")
        args.state_root.mkdir(parents=True, exist_ok=True)
        security = SecurityRuntime(args.state_root / ".security")
        security.authorize("security.manage", "security:satellite_gateway")
        subject = "service:satellite:" + args.source_id
        security.grant_identity(subject, ["operator"], ["source:" + args.source_id + "/*"])
        security.vault.put_bytes(
            "orion-mission-session",
            security.issue_service_session(subject, ttl_seconds=min(86400, args.duration + 1)).encode(
                "ascii"
            ),
        )
        store = MissionFrameStore(
            args.state_root / "mission.sqlite",
            source_id=args.source_id,
            origin=args.origin,
            allowed_satellite_ids=[args.satellite_id],
            allowed_mission_bindings={
                args.satellite_id: {
                    "mission_id": args.mission_id,
                    "norad_catalog_id": args.norad,
                    "decoder_profile": profile,
                }
            },
        )
        server = MissionWebSocketServer(
            store, time.time, port=args.port, security=security, credential_ref="vault:orion-mission-session"
        )
        initial = {
            "state": "listening",
            "url": f"ws://127.0.0.1:{server.port}/satellite/v1/",
            "security_root": str(args.state_root / ".security"),
            "credential_ref": "vault:orion-mission-session",
            "source_id": args.source_id,
            "origin": args.origin,
            "physical_actuation": False,
        }
        if sys.stdout is not None:
            print(json.dumps(initial), flush=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
        try:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
        result = {**initial, "state": "stopped", "ingress": store.snapshot()}
    else:
        if args.outbox is None:
            raise ValueError("--outbox is required for acquisition/delivery")
        outbox = GatewayOutbox(args.outbox, source_id=args.source_id, origin=args.origin)
        if args.action == "import-jsonl":
            with args.path.open("rb") as stream:
                count = 0
                while line := stream.readline(32 * 1024 + 1):
                    if len(line) > 32 * 1024:
                        raise ValueError("archived frame envelope exceeds gateway limit")
                    value = strict_object(line)
                    value.pop("sequence", None)  # This gateway owns its independent delivery sequence.
                    outbox.enqueue(value)
                    count += 1
            result = {"state": "queued", "imported": count}
        elif args.action == "satnogs":
            if args.origin != "measurement_file" or args.norad != 43804:
                raise ValueError(
                    "implemented public Suomi100 adapter requires measurement_file and NORAD43804"
                )
            if type(args.polls) is not int or not 1 <= args.polls <= 60 or not 30 <= args.interval <= 3600:
                raise ValueError("explicit public polling: 1..60 polls, 30..3600 second interval")
            client = PublicFrameClient(timeout=args.timeout)
            count = 0
            for index in range(args.polls):
                if index:
                    time.sleep(args.interval)
                records = client.acquire_observation(
                    args.observation,
                    norad_id=args.norad,
                    satellite_id=args.satellite_id,
                    source_id=args.source_id,
                    profile=profile,
                    known_receptions=outbox.known_receptions(),
                )
                for record in records:
                    outbox.enqueue(record)
                    count += 1
            result = {"state": "queued", "public_frames": count, "polls": args.polls}
        elif args.action in ("kiss-file", "kiss-tcp"):
            from .satellite_radio import iter_kiss_file, receive_kiss_tcp

            status = {}
            options = dict(
                max_frames=args.max_frames, timeout_s=args.timeout, max_bytes=args.max_bytes, report=status
            )
            capture_hash = None
            count = 0
            with ExitStack() as resources:
                if args.action == "kiss-file":
                    if args.origin != "measurement_file":
                        raise ValueError("KISS recording must retain measurement_file origin")
                    frozen, capture_hash = resources.enter_context(
                        capture_snapshot(
                            args.path,
                            directory=outbox.path.parent,
                            max_bytes=args.max_bytes,
                            timeout_s=args.timeout,
                        )
                    )
                    capture_id = "kiss-file:" + capture_hash[:32]
                    records = iter_kiss_file(frozen, captured_at_utc=args.captured_at, **options)
                else:
                    capture_id = args.capture_id
                    records = receive_kiss_tcp(args.host, args.port, **options)
                with closing(records):
                    for index, record in enumerate(records):
                        packet = radio_envelope(
                            record,
                            source_id=args.source_id,
                            origin=args.origin,
                            satellite_id=args.satellite_id,
                            mission_id=args.mission_id,
                            norad_id=args.norad,
                            profile=profile,
                            station_id=args.station_id,
                            station_name=args.station_name,
                            capture_id=capture_id,
                            index=index,
                            capture_sha256=capture_hash,
                        )
                        outbox.enqueue(packet)
                        count += 1
            failed = status.get("termination") in ("file_error", "connect_error", "socket_error")
            result = {
                "state": "unavailable" if failed else "queued",
                "frames_queued": count,
                "capture": status,
            }
        else:
            from websockets.exceptions import ConnectionClosed, InvalidStatus

            if (
                not 0 <= args.follow_seconds <= 3600
                or not 0.1 <= args.interval <= 60
                or not 1 <= args.batch_size <= 256
            ):
                raise ValueError("delivery duration/batch/interval outside operational limits")
            security = SecurityRuntime(args.security_root, bootstrap=False)
            security.authorize("ingestion.write", "source:" + args.source_id + "/frames")
            client = MissionGatewayClient(args.url, security=security, credential_ref=args.credential_ref)
            initial_acknowledged = outbox.statistics().get("acknowledged", 0)
            failures, blocked, last_error = 0, False, None
            deadline = time.monotonic() + args.follow_seconds
            try:
                while True:
                    if args.follow_seconds and time.monotonic() >= deadline:
                        break
                    try:
                        send_deadline = deadline if args.follow_seconds else None
                        attempted = 0

                        def send(body):
                            nonlocal attempted
                            attempted += 1
                            security.authorize("ingestion.write", "source:" + args.source_id + "/frames")
                            remaining = (
                                9.0
                                if send_deadline is None
                                else min(9.0, max(0.0, send_deadline - time.monotonic()))
                            )
                            if not remaining:
                                raise TimeoutError("gateway delivery deadline")
                            return client.send(body, timeout_s=remaining)

                        outbox.flush(send, limit=args.batch_size, deadline=send_deadline)
                        if attempted:
                            last_error = None
                    except (OSError, TimeoutError, ConnectionClosed, InvalidStatus) as exc:
                        failures += 1
                        last_error = type(exc).__name__
                        blocked = (
                            isinstance(exc, PermissionError)
                            or isinstance(exc, InvalidStatus)
                            and exc.response.status_code in (401, 403)
                        )
                    if blocked or args.follow_seconds == 0 or time.monotonic() >= deadline:
                        break
                    time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))
            except KeyboardInterrupt:
                last_error = "Cancelled"
            termination = (
                "identity_blocked"
                if blocked
                else "cancelled"
                if last_error == "Cancelled"
                else "deadline"
                if args.follow_seconds and time.monotonic() >= deadline
                else "batch_complete"
            )
            result = {
                "state": "unavailable" if last_error else "delivered",
                "acknowledged": outbox.statistics().get("acknowledged", 0) - initial_acknowledged,
                "transport_failures": failures,
                "identity_blocked": blocked,
                "last_error_type": last_error,
                "termination": termination,
                "socket_shutdown_grace_s": 1.0,
                "vault_lock_timeout_s": 15.0,
                "deadline_scope": "cooperative; no new frame after deadline, OS/vault locks may delay shutdown",
                "auto_session_renewal": False,
            }
        result.update(
            source_id=args.source_id,
            origin=args.origin,
            pending=outbox.statistics().get("pending", 0),
            physical_actuation=False,
        )
        if args.action == "deliver" and result["state"] == "delivered" and result["pending"]:
            result["state"] = "delivery_pending"
    result["at_utc"] = datetime.now(timezone.utc).isoformat()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(json.dumps(result, allow_nan=False), flush=True)
    return 2 if result["state"] == "unavailable" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, PermissionError, OSError, RuntimeError) as error:
        # Print no URLs with credentials, input body, bearer token or raw exception.
        if sys.stderr is not None:
            print(json.dumps({"state": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(2)
