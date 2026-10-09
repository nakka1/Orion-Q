"""Independent emulated KME and application processes over real loopback TLS.

This is an ETSI-014 subset plus an explicitly named laboratory peer-control
protocol. ETSI-014 does not standardize KME provisioning, delivery acknowledgments
or retry idempotency. Test fault injection is process-owned and never an HTTP API.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from .kms import (KeyDeliveryError, _authorize_certificate, _reject_constant, _server_context,
                  _strict_object, client_context, create_test_pki, request_kme)
from .persistent_keys import KeyLedger, validate_delivery_token, validate_enc_request
from .qkd import run_bb84
from quantum_autopilot.security.key_requirements import check_kme_security, required_kme_identities


@contextmanager
def _exclusive_owner(path):
    """OS-held lock is automatically released even when the KME process is killed."""
    handle = open(str(path) + ".owner.lock", "a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield
    finally:
        handle.close()


class _DroppedResponse(Exception):
    pass


def _worker(control, path, wrapping_key, pki, identity, provenance, initial_pairs, security_configuration=None):
    server = None
    try:
        with _exclusive_owner(path):
            runtime = None
            if security_configuration is not None:
                from quantum_autopilot.security.runtime import SecurityRuntime
                runtime = SecurityRuntime(**security_configuration, bootstrap=False)
            if pki.get("_profile") != "emulation":
                raise ValueError("emulated ledger cannot be exposed as operational key delivery")
            ledger = KeyLedger(path, wrapping_key, identity=identity, security_runtime=runtime, **provenance)
            if initial_pairs is not None:
                ledger.provision(initial_pairs)
            initial_pairs = None
            ledger.recover()
            state = {"peer_port": None, "fault": None, "paused_phase": None, "delay_s": 0.0,
                     "acquisition_blocked": False}
            state_lock = threading.Lock()
            peer_identity = "kme-bob" if identity == "kme-alice" else "kme-alice"

            def peer_call(operation, token, ids):
                with state_lock:
                    port = state["peer_port"]
                if port is None:
                    raise KeyDeliveryError(503, "peer KME unavailable")
                connection = http.client.HTTPSConnection("127.0.0.1", port,
                    context=client_context(pki, identity), timeout=10)
                try:
                    connection.connect()
                    _authorize_certificate(pki, connection.sock.getpeercert(binary_form=True), "kme-peer",
                                           expected_identity=peer_identity, expected_kind="kme", usage="server")
                    payload = json.dumps({"token": token, "ids": ids}).encode("utf-8")
                    connection.request("POST", "/internal/orion-lab/v1/" + operation, body=payload,
                                       headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    response.read()
                    if response.status != 200:
                        raise KeyDeliveryError(503, "peer reservation coordination rejected")
                finally:
                    connection.close()

            def reconcile_invalidations():
                from contextlib import nullcontext
                service_scope = runtime.identity_scope(runtime.principal(identity)) if runtime is not None else nullcontext()
                with service_scope:
                    for token, ids in ledger.pending_invalidations():
                        peer_call("invalidate", token, ids)
                        ledger.acknowledge_invalidation(token)

            def checkpoint(handler, phase):
                with state_lock:
                    fault = state["fault"]
                    if not fault or fault["phase"] != phase:
                        return
                    state["fault"] = None
                    action = fault["action"]
                    if action == "pause":
                        state["paused_phase"] = phase
                if action == "pause":
                    # A bounded wait allows the controller to terminate the process
                    # at the exact durable boundary, instead of pretending a crash.
                    threading.Event().wait(20)
                if action == "unexpected_error":
                    raise RuntimeError("injected local state failure")
                try:
                    handler.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                handler.connection.close()
                raise _DroppedResponse()

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def setup(self):
                    super().setup()
                    self.connection.settimeout(15)

                def log_message(self, *_args):
                    pass

                def body(self):
                    if self.headers.get_content_type() != "application/json":
                        raise KeyDeliveryError(400, "application/json required")
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 1 <= length <= 65536:
                            raise ValueError()
                        return json.loads(self.rfile.read(length), object_pairs_hook=_strict_object,
                                          parse_constant=_reject_constant)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise KeyDeliveryError(400, "invalid JSON request") from exc

                def respond(self, status, data, *, token=None):
                    encoded = json.dumps(data, allow_nan=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=UTF-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if token:
                        checkpoint(self, "after_response_headers")
                        with state_lock:
                            mid = state["fault"] and state["fault"]["phase"] == "mid_response"
                        if mid:
                            self.wfile.write(encoded[:len(encoded) // 2])
                            self.wfile.flush()
                            checkpoint(self, "mid_response")
                    self.wfile.write(encoded)
                    self.wfile.flush()
                    self.close_connection = True
                    if token and identity == "kme-alice":
                        ledger.response_sent(token)

                def process(self):
                    from contextlib import nullcontext
                    try:
                        internal = self.path.startswith("/internal/orion-lab/v1/")
                        caller = _authorize_certificate(pki, self.connection.getpeercert(binary_form=True),
                            "kme-peer" if internal else "key-delivery", expected_kind="kme" if internal else "sae")
                        scope = nullcontext()
                        if runtime is not None:
                            principal = runtime.principal(caller)
                            scope = runtime.identity_scope(principal)
                        with scope:
                            # Every invoked ledger method revalidates its own
                            # current grant and durably audits before side effects.
                            # An identical outer audit would serialize twice and
                            # add no independent authorization decision.
                            self._process()
                    except (KeyDeliveryError, PermissionError) as error:
                        self.respond(error.status if isinstance(error, KeyDeliveryError) else 403,
                                     {"message": "certificate identity or key resource permission rejected"})

                def _process(self):
                    token, ids = None, []
                    try:
                        caller = _authorize_certificate(pki, self.connection.getpeercert(binary_form=True),
                            "kme-peer" if self.path.startswith("/internal/orion-lab/v1/") else "key-delivery")
                        parts = urlsplit(self.path)
                        if parts.query:
                            raise KeyDeliveryError(404, "query not supported")
                        if parts.path.startswith("/internal/orion-lab/v1/"):
                            if self.command != "POST" or caller != peer_identity:
                                raise KeyDeliveryError(401, "peer-control caller rejected")
                            body = self.body()
                            if not isinstance(body, dict) or set(body) != {"token", "ids"}:
                                raise KeyDeliveryError(400, "invalid peer-control message")
                            operation = parts.path.rsplit("/", 1)[1]
                            with state_lock:
                                blocked = state["acquisition_blocked"]
                            if blocked and operation != "invalidate":
                                raise KeyDeliveryError(503, "peer key acquisition blocked after local state failure")
                            if operation == "reserve":
                                ledger.reserve_peer(body["ids"], body["token"])
                            elif operation == "authorize":
                                ledger.authorize_peer(body["ids"], body["token"])
                            elif operation == "invalidate":
                                ledger.invalidate(body["token"], body["ids"])
                            else:
                                raise KeyDeliveryError(404, "unknown peer-control operation")
                            self.respond(200, {"accepted": True})
                            return
                        route = parts.path.strip("/").split("/")
                        if len(route) != 5 or route[:3] != ["api", "v1", "keys"]:
                            raise KeyDeliveryError(404, "unknown key-delivery route")
                        peer, operation = unquote(route[3]), route[4]
                        caller_id, peer_id = (("sae-alice", "sae-bob") if identity == "kme-alice"
                                              else ("sae-bob", "sae-alice"))
                        if caller != caller_id or peer != peer_id:
                            raise KeyDeliveryError(401, "SAE identity or peer association rejected")
                        if self.command == "GET" and operation == "status":
                            summary = ledger.summary()
                            with state_lock:
                                blocked = state["acquisition_blocked"]
                            self.respond(200, {"source_KME_ID": identity, "target_KME_ID": peer_identity,
                                "master_SAE_ID": "sae-alice", "slave_SAE_ID": "sae-bob", "key_size": 256,
                                "stored_key_count": 0 if blocked else summary["counts"].get("available", 0),
                                "max_key_count": summary["capacity"], "max_key_per_request": 64,
                                "max_key_size": 256, "min_key_size": 256, "max_SAE_ID_count": 0,
                                "status_extension": {"acquisition_blocked": blocked}})
                            return
                        if self.command == "GET" and operation == "delivery_status":
                            self.respond(200, ledger.delivery_status(self.headers.get("X-Orion-Delivery-Token")))
                            return
                        if self.command != "POST" or operation not in {"enc_keys", "dec_keys"}:
                            raise KeyDeliveryError(405, "method or operation unsupported")
                        body = self.body()
                        token = validate_delivery_token(self.headers.get("X-Orion-Delivery-Token") or str(uuid4()))
                        with state_lock:
                            delay = state["delay_s"]
                            blocked = state["acquisition_blocked"]
                        if blocked:
                            raise KeyDeliveryError(503, "key acquisition blocked after local state failure")
                        if delay:
                            threading.Event().wait(delay)
                        if operation == "enc_keys" and identity == "kme-alice":
                            validate_enc_request(body)
                            reconcile_invalidations()
                            checkpoint(self, "before_reservation")
                            ids = ledger.reserve(body, token)
                            checkpoint(self, "after_local_reservation")
                            peer_call("reserve", token, ids)
                            checkpoint(self, "after_peer_reservation")
                            result = ledger.commit_issue(token)
                            checkpoint(self, "after_local_commit")
                            peer_call("authorize", token, ids)
                            checkpoint(self, "after_peer_commit")
                        elif operation == "dec_keys" and identity == "kme-bob":
                            result = ledger.consume(body, token)
                            checkpoint(self, "after_slave_commit")
                        else:
                            raise KeyDeliveryError(401, "operation not authorized for this KME")
                        self.respond(200, result, token=token)
                    except Exception as error:
                        if not isinstance(error, (KeyDeliveryError, PermissionError, OSError, http.client.HTTPException, _DroppedResponse)):
                            with state_lock:
                                state["acquisition_blocked"] = True
                        if ids:
                            try:
                                from contextlib import nullcontext
                                cleanup_scope = (runtime.identity_scope(runtime.principal(identity))
                                                 if runtime is not None else nullcontext())
                                with cleanup_scope:
                                    ledger.invalidate(token, ids, reconcile_peer=True)
                                    reconcile_invalidations()
                            except (KeyDeliveryError, OSError, http.client.HTTPException):
                                pass
                            except Exception:
                                with state_lock:
                                    state["acquisition_blocked"] = True
                        if isinstance(error, _DroppedResponse):
                            self.close_connection = True
                            return
                        try:
                            self.respond(error.status if isinstance(error, KeyDeliveryError) else
                                         403 if isinstance(error, PermissionError) else 503,
                                         {"message": error.message if isinstance(error, KeyDeliveryError)
                                          else "key resource permission rejected" if isinstance(error, PermissionError)
                                          else "peer unavailable or delivery transport failed"})
                        except (OSError, http.client.HTTPException):
                            self.close_connection = True

                do_GET = process
                do_POST = process

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            # Graceful shutdown retains exclusive ownership until HTTP handlers
            # finish. Hard termination is covered separately by restart recovery.
            server.daemon_threads = False
            server.socket = _server_context(pki, identity).wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .025}, daemon=True)
            thread.start()
            control.send({"ready": True, "port": server.server_address[1], "pid": os.getpid()})
            while True:
                message = control.recv()
                operation = message["operation"]
                if operation == "stop":
                    break
                if operation in {"peer_port", "fault", "delay_s"}:
                    with state_lock:
                        state[operation] = message["value"]
                    control.send({"accepted": True})
                elif operation == "summary":
                    control.send({**ledger.summary(), "pid": os.getpid(), "paused_phase": state["paused_phase"],
                                  "acquisition_blocked": state["acquisition_blocked"]})
                elif operation == "reconcile":
                    try:
                        reconcile_invalidations()
                        control.send({"accepted": True})
                    except (KeyDeliveryError, OSError, http.client.HTTPException):
                        control.send({"accepted": False})
                else:
                    control.send({"accepted": False})
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            server = None
    except BaseException as error:
        try:
            control.send({"ready": False, "error_type": type(error).__name__})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if server:
            server.shutdown()
            server.server_close()
        control.close()


class IndependentKme:
    def __init__(self, path, wrapping_key, pki, identity, provenance, *, pairs=None, security_runtime=None):
        self.args = (str(path), wrapping_key, pki, identity, provenance)
        self.security_configuration = ({"root": str(security_runtime.root), "profile": security_runtime.profile}
                                       if security_runtime is not None else None)
        self.context = multiprocessing.get_context("spawn")
        self.process = None
        self.port = None
        self.start(pairs=pairs)

    def start(self, *, pairs=None):
        parent, child = self.context.Pipe()
        self.control = parent
        self.process = self.context.Process(target=_worker,
            args=(child, *self.args, pairs, self.security_configuration), daemon=True)
        self.process.start()
        child.close()
        if not parent.poll(30):
            self.kill()
            raise RuntimeError("independent KME startup timeout")
        result = parent.recv()
        if not result.get("ready"):
            self.kill()
            raise RuntimeError("independent KME failed to initialize: " + result.get("error_type", "unknown"))
        self.port, self.pid = result["port"], result["pid"]

    def command(self, operation, value=None):
        self.control.send({"operation": operation, "value": value})
        if not self.control.poll(10):
            raise RuntimeError("independent KME control timeout")
        return self.control.recv()

    def kill(self):
        if self.process and self.process.is_alive():
            self.process.terminate()
        if self.process:
            self.process.join(timeout=10)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
        if hasattr(self, "control"):
            self.control.close()

    def close(self):
        if self.process and self.process.is_alive():
            self.control.send({"operation": "stop"})
            self.process.join(timeout=10)
        self.kill()


@contextmanager
def independent_pair(directory, pki, pairs, provenance, *, security_runtime=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    alice = bob = None
    def wrapping_key(identity):
        if security_runtime is None:
            return secrets.token_bytes(32)
        import hashlib
        reference = "key-storage-wrap:" + identity + ":" + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()
        return security_runtime.vault.get_or_create_bytes(reference, 32)
    try:
        alice = IndependentKme(directory / "alice.sqlite", wrapping_key("kme-alice"), pki, "kme-alice", provenance,
                               pairs=pairs, security_runtime=security_runtime)
        bob = IndependentKme(directory / "bob.sqlite", wrapping_key("kme-bob"), pki, "kme-bob", provenance,
                             pairs=pairs, security_runtime=security_runtime)
        alice.command("peer_port", bob.port)
        bob.command("peer_port", alice.port)
        yield alice, bob
    finally:
        if alice:
            alice.close()
        if bob:
            bob.close()


def _consumer_worker(control, identity, pki, port, app_port=None):
    """A distinct application owns its own plaintext and KME connection."""
    try:
        if identity == "sae-bob":
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(15)
            listener = _server_context(pki, identity).wrap_socket(listener, server_side=True)
            control.send({"ready": True, "port": listener.getsockname()[1], "pid": os.getpid()})
            with listener, listener.accept()[0] as channel:
                channel.settimeout(15)
                _authorize_certificate(pki, channel.getpeercert(binary_form=True), "application-transfer",
                                       expected_identity="sae-alice", expected_kind="sae")
                reader = channel.makefile("rb")
                message = json.loads(reader.readline(65536))
                dec = request_kme(port, client_context(pki, identity), "kme-bob", "POST", "sae-alice", "dec_keys",
                                  {"key_IDs": [{"key_ID": message["key_ID"]}]})
                if dec["status"] != 200:
                    raise RuntimeError("peer key not authorized")
                entry = dec["body"]["keys"][0]
                material = base64.b64decode(entry["key"], validate=True)
                plaintext = AESGCM(material).decrypt(base64.b64decode(message["nonce"]),
                    base64.b64decode(message["ciphertext"]), message["key_ID"].encode("ascii"))
                tamper_rejected = False
                corrupted = base64.b64decode(message["ciphertext"])
                try:
                    AESGCM(material).decrypt(base64.b64decode(message["nonce"]), corrupted[:-1] +
                        bytes([corrupted[-1] ^ 1]), message["key_ID"].encode("ascii"))
                except InvalidTag:
                    tamper_rejected = True
                ack = {"matched_id": entry["key_ID"] == message["key_ID"],
                       "plaintext_matched": plaintext == b"ORION-Q identified TEST payload",
                       "tamper_rejected": tamper_rejected}
                channel.sendall(json.dumps(ack).encode() + b"\n")
                control.send({"passed": all(ack.values()), "pid": os.getpid(), "consumed_keys": 1,
                              "latency_ms": dec["latency_ms"], "tls_version": dec["tls_version"]})
        else:
            enc = request_kme(port, client_context(pki, identity), "kme-alice", "POST", "sae-bob", "enc_keys", {})
            if enc["status"] != 200:
                raise RuntimeError("master key unavailable")
            entry = enc["body"]["keys"][0]
            material = base64.b64decode(entry["key"], validate=True)
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(material).encrypt(nonce, b"ORION-Q identified TEST payload",
                                                entry["key_ID"].encode("ascii"))
            message = {"key_ID": entry["key_ID"], "nonce": base64.b64encode(nonce).decode(),
                       "ciphertext": base64.b64encode(ciphertext).decode()}
            raw_channel = socket.create_connection(("127.0.0.1", app_port), timeout=15)
            with client_context(pki, identity).wrap_socket(raw_channel, server_hostname="localhost") as channel:
                _authorize_certificate(pki, channel.getpeercert(binary_form=True), "application-transfer",
                                       expected_identity="sae-bob", expected_kind="sae", usage="server")
                channel.sendall(json.dumps(message).encode() + b"\n")
                ack = json.loads(channel.makefile("rb").readline(65536))
            control.send({"passed": all(ack.values()), "pid": os.getpid(), "consumed_keys": 1,
                          "latency_ms": enc["latency_ms"], "tls_version": enc["tls_version"]})
    except BaseException as error:
        control.send({"passed": False, "error_type": type(error).__name__, "pid": os.getpid()})
    finally:
        control.close()


def run_consumers(alice, bob, pki):
    context = multiprocessing.get_context("spawn")
    bob_parent, bob_child = context.Pipe()
    a_parent, a_child = context.Pipe()
    process_b = context.Process(target=_consumer_worker, args=(bob_child, "sae-bob", pki, bob.port), daemon=True)
    process_a = None
    try:
        process_b.start()
        bob_child.close()
        if not bob_parent.poll(30):
            raise RuntimeError("application startup timeout")
        ready = bob_parent.recv()
        if not ready.get("ready"):
            raise RuntimeError("receiver application initialization failed")
        process_a = context.Process(target=_consumer_worker,
            args=(a_child, "sae-alice", pki, alice.port, ready["port"]), daemon=True)
        process_a.start()
        a_child.close()
        if not a_parent.poll(30) or not bob_parent.poll(30):
            raise RuntimeError("independent application communication timeout")
        return a_parent.recv(), bob_parent.recv()
    finally:
        for process in (process_a, process_b):
            if process:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        a_parent.close()
        bob_parent.close()


def run_delivery_fault_campaign(directory, pki, provenance, *, security=None):
    checks = {}
    phases = ("before_reservation", "after_local_reservation", "after_peer_reservation", "after_local_commit",
              "after_peer_commit", "after_response_headers", "mid_response")
    pairs = [(str(uuid4()), secrets.token_bytes(32)) for _ in range(16)]
    with independent_pair(directory, pki, pairs, provenance, security_runtime=security) as (alice, bob):
        ca = client_context(pki)
        for phase in phases:
            token = "fault-" + phase.replace("_", "-")
            alice.command("fault", {"phase": phase, "action": "drop"})
            before = alice.command("summary")["counts"].get("available", 0)
            try:
                request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {}, delivery_token=token)
                lost = False
            except (OSError, http.client.HTTPException, ValueError):
                lost = True
            if phase != "before_reservation":
                # Socket closure can reach the client before the handler finishes
                # its durable invalidation. Wait for that explicit state.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    recorded = request_kme(alice.port, ca, "kme-alice", "GET", "sae-bob", "delivery_status",
                                           delivery_token=token)
                    if recorded["body"].get("state") == "invalidated":
                        break
                    threading.Event().wait(.01)
            after = alice.command("summary")["counts"].get("available", 0)
            if phase == "before_reservation":
                checks[phase] = lost and before == after
            else:
                again = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {}, delivery_token=token)
                status = request_kme(alice.port, ca, "kme-alice", "GET", "sae-bob", "delivery_status",
                                     delivery_token=token)
                checks[phase] = lost and after == before - 1 and again["status"] == 409 and \
                    status["body"]["state"] == "invalidated" and not status["body"]["material_recoverable"]
        # Interrupt a real process while SQLite reservation is durable.
        token = "terminated-after-reservation"
        alice.command("fault", {"phase": "after_peer_reservation", "action": "pause"})
        def pending_delivery():
            try:
                request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {}, delivery_token=token)
            except (OSError, http.client.HTTPException):
                return True
            return False
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(pending_delivery)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if alice.command("summary")["paused_phase"] == "after_peer_reservation":
                    break
                threading.Event().wait(.03)
            else:
                raise RuntimeError("crash-boundary fixture not reached")
            prior_pid = alice.pid
            alice.kill()
            lost = future.result(timeout=8)
        alice.start()
        alice.command("peer_port", bob.port)
        bob.command("peer_port", alice.port)
        reconciliation = alice.command("reconcile")
        status = request_kme(alice.port, ca, "kme-alice", "GET", "sae-bob", "delivery_status", delivery_token=token)
        checks["process_killed_reservation_recovered_without_reuse"] = lost and alice.pid != prior_pid and \
            reconciliation["accepted"] and status["body"]["state"] == "invalidated"
        # Stop the remote process, producing a real localhost partition.
        bob.kill()
        before = alice.command("summary")["counts"].get("available", 0)
        partition = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {},
                                delivery_token="partitioned-peer")
        summary = alice.command("summary")
        checks["partition_burns_reservation_and_persists_invalidation"] = partition["status"] == 503 and \
            summary["counts"].get("available", 0) == before - 1 and summary["pending_peer_invalidations"] == 1
        blocked = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
        checks["new_delivery_blocked_until_peer_reconciliation"] = blocked["status"] == 503 and \
            alice.command("summary")["counts"].get("available", 0) == before - 1
        bob.start()
        bob.command("peer_port", alice.port)
        alice.command("peer_port", bob.port)
        checks["independent_peer_restart_reconciles"] = alice.command("reconcile")["accepted"] and \
            alice.command("summary")["pending_peer_invalidations"] == 0
        alice.command("delay_s", .08)
        delayed = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
        checks["injected_delay_preserves_assignment"] = delayed["status"] == 200 and delayed["latency_ms"] >= 70
        alice.command("delay_s", 0)
        entry = delayed["body"]["keys"][0]
        bob.command("fault", {"phase": "after_slave_commit", "action": "drop"})
        body = {"key_IDs": [{"key_ID": entry["key_ID"]}]}
        try:
            request_kme(bob.port, client_context(pki, "sae-bob"), "kme-bob", "POST", "sae-alice", "dec_keys", body)
            lost = False
        except (OSError, http.client.HTTPException):
            lost = True
        repeated = request_kme(bob.port, client_context(pki, "sae-bob"), "kme-bob", "POST", "sae-alice", "dec_keys", body)
        checks["slave_response_loss_never_reissues_consumed_key"] = lost and repeated["status"] == 400
        alice.command("fault", {"phase": "after_peer_reservation", "action": "unexpected_error"})
        error = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
        status = request_kme(alice.port, ca, "kme-alice", "GET", "sae-bob", "status")
        blocked = request_kme(alice.port, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
        checks["unexpected_local_failure_blocks_acquisition"] = error["status"] == 503 and blocked["status"] == 503 and \
            status["body"]["stored_key_count"] == 0 and status["body"]["status_extension"]["acquisition_blocked"]
        summaries = [alice.command("summary"), bob.command("summary")]
    return {"checks": checks, "all_passed": all(checks.values()), "ledgers": summaries,
            "fault_phases": list(phases), "physical_action_performed": False, "certified_secret_bits": 0}


def run_independent_qkd_demo(output_directory, *, config=None, security=None, principal=None):
    if security is not None:
        # Fixed TEST identities must be administratively provisioned beforehand;
        # a demonstration request never creates or widens a persistent grant.
        if not check_kme_security(security)["available"]:
            raise PermissionError("administratively provisioned TEST SAE/KME identities and resource grants required")
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    session = run_bb84(config, observation_path=output_directory / "observations.json",
                      security=security, principal=principal)
    initial_keys = min(10, len(session.delivery_material) // 32)
    if not initial_keys:
        report = {"schema": "orion.lab.qkd-delivery-run.v2", "mode": "emulation", "status": "protocol_aborted",
            "physical_acquisition": False, "physical_action_performed": False, "all_passed": True,
            "qkd_session": session.summary, "checks": {"aborted_protocol_provisions_no_keys": True},
            "delivery": {"initial_keys": 0, "master_consumed_keys": 0, "slave_consumed_keys": 0,
                         "buffer_remaining_keys": 0, "kmes": 0, "consumer_applications": 0},
            "limits": ["protocol abort is an observed outcome; no key-delivery operation was performed"]}
        (output_directory / "qkd_report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        return report
    provenance = {"experiment_id": session.summary["experiment_id"], "model_id": session.summary["model_id"]}
    pairs = [(str(uuid4()), session.delivery_material[offset:offset + 32]) for offset in range(0, initial_keys * 32, 32)]
    run_directory = output_directory / ("durable-run-" + str(uuid4()))
    checks, latencies = {}, []
    with tempfile.TemporaryDirectory(prefix="qkd-pki-", dir=output_directory) as temporary:
        pki = create_test_pki(temporary)
        with independent_pair(run_directory / "independent-kmes", pki, pairs, provenance,
                              security_runtime=security) as (alice, bob):
            status = request_kme(alice.port, client_context(pki), "kme-alice", "GET", "sae-bob", "status")
            checks["status_schema_and_initial_buffer"] = status["status"] == 200 and status["body"]["stored_key_count"] == initial_keys
            app_a, app_b = run_consumers(alice, bob, pki)
            pids = [alice.pid, bob.pid, app_a["pid"], app_b["pid"]]
            checks["four_independent_kme_and_application_processes"] = len(set(pids)) == 4 and os.getpid() not in pids
            checks["matching_key_material_id_aes_gcm_and_tamper"] = app_a["passed"] and app_b["passed"]
            checks["distinct_persistent_endpoint_stores"] = alice.args[0] != bob.args[0]
            latencies.extend([app_a["latency_ms"], app_b["latency_ms"]])
            tls = app_a["tls_version"]
            wrong = request_kme(alice.port, client_context(pki, "sae-bob"), "kme-alice", "POST", "sae-bob", "enc_keys", {})
            checks["valid_certificate_wrong_sae_rejected"] = wrong["status"] == 401
            try:
                request_kme(alice.port, client_context(pki), "kme-bob", "GET", "sae-bob", "status")
                checks["wrong_kme_identity_rejected"] = False
            except KeyDeliveryError:
                checks["wrong_kme_identity_rejected"] = True
            for label, identity in (("expired_certificate", "sae-expired"), ("missing_certificate", None)):
                try:
                    request_kme(alice.port, client_context(pki, identity), "kme-alice", "GET", "sae-bob", "status")
                    checks[label + "_rejected"] = False
                except (OSError, http.client.HTTPException):
                    checks[label + "_rejected"] = True
            def consume(_index):
                return request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})
            with ThreadPoolExecutor(max_workers=6) as workers:
                concurrent = list(workers.map(consume, range(12)))
            successes = [item for item in concurrent if item["status"] == 200]
            ids = [item["body"]["keys"][0]["key_ID"] for item in successes]
            checks["atomic_concurrency_no_duplicate_assignment"] = len(ids) == len(set(ids)) == initial_keys - 1
            checks["buffer_exhaustion"] = sum(item["status"] == 503 for item in concurrent) == 12 - (initial_keys - 1)
            summary_a, summary_b = alice.command("summary"), bob.command("summary")
            old_pid = alice.pid
            alice.kill()
            alice.start()
            alice.command("peer_port", bob.port)
            bob.command("peer_port", alice.port)
            after = request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})
            checks["full_process_restart_does_not_reissue_consumed_keys"] = after["status"] == 503 and alice.pid != old_pid
            latencies.extend(item["latency_ms"] for item in successes)
        fault_campaign = run_delivery_fault_campaign(run_directory / "delivery-faults", pki,
            {"experiment_id": provenance["experiment_id"] + ":delivery-faults",
             "model_id": "orion.lab.delivery-random-test-fixture.v1"}, security=security)
        checks["delivery_faults_partition_and_process_recovery"] = fault_campaign["all_passed"]
    report = {
        "schema": "orion.lab.qkd-delivery-run.v2", "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "emulation", "physical_acquisition": False, "physical_action_performed": False,
        "validation_levels": ["identified_physical_model", "observed_protocol", "independent_processes",
                              "https_mtls_interface", "durable_failure_recovery"],
        "qkd_session": session.summary, "checks": checks, "all_passed": all(checks.values()),
        "key_source": {**provenance, "origin": "emulated observed BB84 reconciliation and Toeplitz output",
                       "certified": False},
        "delivery": {"standard": "ETSI GS QKD 014 V1.1.1 subset", "transport": "localhost HTTPS/mTLS",
            "tls_version": tls, "kmes": 2, "consumer_applications": 2, "independent_processes": 4,
            "application_transport": "localhost TCP/TLS mutual certificates + AES-GCM ciphertext/AAD",
            "key_size_bits": 256, "initial_keys": initial_keys, "master_consumed_keys": summary_a["counts"].get("issued", 0),
            "slave_consumed_keys": summary_b["counts"].get("consumed", 0),
            "buffer_remaining_keys": summary_a["counts"].get("available", 0),
            "latency_ms_min": min(latencies), "latency_ms_max": max(latencies),
            "latency_ms_mean": sum(latencies) / len(latencies), "successful_deliveries_timed": len(latencies),
            "concurrent_requests": 12, "concurrent_successes": len(successes),
            "delivery_policy": "irreversible reservation; consume before response; uncertain delivery invalidates",
            "retry_extension": "X-Orion-Delivery-Token + delivery_status; status only, never re-deliver material"},
        "endpoint_ledgers": [summary_a, summary_b], "fault_campaign": fault_campaign,
        "limits": ["identified TEST bytes only; PCG64 model seed is public and insecure",
            "no physical QKD appliance or certified secret-key proof", "ETSI subset, not interoperability certification",
            "peer control and delivery-status extensions are laboratory contracts, not ETSI-014 endpoints",
            "two local endpoint copies are provisioned from an explicitly centralized emulated QKD source",
            ("AES-GCM wrapping keys persist in the common OS-protected vault; authorization is required for recovery"
             if security is not None else
             "AES-GCM wrapping keys are ephemeral to this validation; persisted test bytes become unrecoverable after run"),
            "reservation loss wastes keys deliberately; application receipt is never assumed from socket success",
            ("OS-protected vault and laboratory PKI are implemented; institutional provisioning, multicast and hardware authentication are not qualified"
             if security is not None else
             "no production secure vault, provisioned PKI, multicast, hardware authentication or key-recovery service")],
        "references": ["https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf",
                       "https://www.nature.com/articles/ncomms1631"],
    }
    (output_directory / "qkd_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
                                                    encoding="utf-8")
    return report
