"""Bounded Linux process transport for PS/PL correlation; no guessed DMA UAPI.

The application wire format is not an AMD descriptor or a hardware driver.
Local process authentication supplements the service's resource authorization.
Lost/late replies leave ownership uncertain and are never automatically retried.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
from threading import Event, Lock, RLock
import time
from uuid import uuid4

from .photon import AcquisitionError
from .pspl import MAX_EVENTS, MAX_WIRE_BYTES, CorrelationRequest, PSPLStateError, TimestampBlock

MAGIC = b"OQPT"
VERSION = (1, 0)
PREFIX = struct.Struct("<4sHHIIIIQ")
HEADER = struct.Struct("<4sHHIIIIQ32s")
COUNT = struct.Struct("<Q")
MAX_METADATA = 16_384
MAX_BINS = 65_537
MAX_REPLAY_ENTRIES = 4096
PROBE, CORRELATE, PROBED, RESULT, REJECTED = range(1, 6)


def _integer(value, name, upper=2**64 - 1):
    if type(value) is not int or not 0 <= value <= upper:
        raise AcquisitionError(f"invalid bounded {name}")
    return value


def _canonical(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise AcquisitionError("invalid transport metadata") from None
    if not isinstance(value, dict) or len(result) > MAX_METADATA:
        raise AcquisitionError("transport metadata limit exceeded")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AcquisitionError("duplicate transport metadata field")
        result[key] = value
    return result


def encode_frame(kind, sequence, metadata, payload=b""):
    if type(kind) is not int or kind not in {PROBE, CORRELATE, PROBED, RESULT, REJECTED}:
        raise AcquisitionError("unsupported transport message")
    _integer(sequence, "sequence")
    if not isinstance(payload, bytes) or len(payload) > MAX_WIRE_BYTES:
        raise AcquisitionError("transport payload limit exceeded")
    meta = _canonical(metadata)
    prefix = PREFIX.pack(MAGIC, *VERSION, kind, 0, len(meta), len(payload), sequence)
    digest = hashlib.sha256(prefix + meta + payload).digest()
    return prefix + digest + meta + payload


def decode_frame(data):
    if not isinstance(data, bytes) or not HEADER.size <= len(data) <= HEADER.size + MAX_METADATA + MAX_WIRE_BYTES:
        raise AcquisitionError("invalid transport frame size")
    magic, major, minor, kind, flags, meta_size, payload_size, sequence, checksum = HEADER.unpack_from(data)
    if (magic, major, minor) != (MAGIC, *VERSION) or flags != 0:
        raise AcquisitionError("incompatible transport ABI")
    if kind not in {PROBE, CORRELATE, PROBED, RESULT, REJECTED} or meta_size > MAX_METADATA or payload_size > MAX_WIRE_BYTES:
        raise AcquisitionError("invalid transport header")
    if len(data) != HEADER.size + meta_size + payload_size:
        raise AcquisitionError("partial transport frame")
    if hashlib.sha256(data[:PREFIX.size] + data[HEADER.size:]).digest() != checksum:
        raise AcquisitionError("transport integrity failed")
    try:
        metadata = json.loads(data[HEADER.size:HEADER.size + meta_size], object_pairs_hook=_unique)
    except (UnicodeError, ValueError, RecursionError):
        raise AcquisitionError("invalid transport metadata") from None
    if _canonical(metadata) != data[HEADER.size:HEADER.size + meta_size]:
        raise AcquisitionError("transport metadata is not canonical")
    return kind, sequence, metadata, data[HEADER.size + meta_size:]


@dataclass(frozen=True)
class BackendIdentity:
    backend_id: str
    mode: str
    generation: int
    image_sha256: str
    max_events: int = MAX_EVENTS

    def validate(self):
        if not isinstance(self.backend_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.backend_id) is None:
            raise AcquisitionError("invalid external backend identity")
        if not isinstance(self.mode, str) or self.mode not in {"cpu-external", "emulated-pl-external", "linux-pl-driver"}:
            raise AcquisitionError("unknown external backend mode")
        _integer(self.generation, "generation")
        if not isinstance(self.image_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.image_sha256) is None:
            raise AcquisitionError("backend image digest required")
        if type(self.max_events) is not int or not 1 <= self.max_events <= MAX_EVENTS:
            raise AcquisitionError("invalid external event capacity")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise AcquisitionError("invalid backend readback")
        identity = cls(**value)
        identity.validate()
        return identity


def _linux():
    if not (os.name == "posix" and hasattr(socket, "SO_PEERCRED") and hasattr(socket, "AF_UNIX")):
        raise PSPLStateError("authenticated local transport requires Linux SO_PEERCRED")


def _private_directory(directory, owner):
    _integer(owner, "peer uid", 2**32 - 1)
    value = Path(directory)
    if not value.is_absolute() or ".." in value.parts:
        raise PSPLStateError("private absolute directory required")
    for parent in (value, *value.parents):
        if parent.is_symlink():
            raise PSPLStateError("directory symlink rejected")
    metadata = value.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner or metadata.st_mode & 0o077:
        raise PSPLStateError("directory is not private to peer")
    return value


def _private_path(path, owner, *, existing):
    value = Path(path)
    if not value.is_absolute() or ".." in value.parts or len(os.fsencode(value)) >= 108:
        raise PSPLStateError("private absolute Unix socket path required")
    _private_directory(value.parent, owner)
    if existing:
        metadata = value.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != owner or metadata.st_mode & 0o077:
            raise PSPLStateError("socket ownership/permissions are invalid")
    elif value.exists() or value.is_symlink():
        raise PSPLStateError("refusing to replace an existing socket path")
    return value


def _peer(connection, expected_uid):
    pid, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != expected_uid or pid <= 0:
        raise PSPLStateError("local process peer is unauthorized")
    return {"pid": pid, "uid": uid, "gid": gid}


def _deadline(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not .001 <= seconds <= 60:
        raise AcquisitionError("transport deadline requires finite seconds in [.001,60]")
    return time.monotonic() + float(seconds)


def _read_exact(connection, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("transport deadline exceeded")
        connection.settimeout(remaining)
        part = connection.recv(count - len(result))
        if not part:
            raise PSPLStateError("peer closed a partial transport frame")
        result.extend(part)
    return bytes(result)


def receive_frame(connection, deadline):
    header = _read_exact(connection, HEADER.size, deadline)
    magic, major, minor, kind, flags, metadata, payload, _sequence, _digest = HEADER.unpack(header)
    if (magic, major, minor) != (MAGIC, *VERSION) or flags or kind not in range(PROBE, REJECTED + 1):
        raise AcquisitionError("incompatible transport header")
    if metadata > MAX_METADATA or payload > MAX_WIRE_BYTES:
        raise AcquisitionError("oversized transport header")
    return decode_frame(header + _read_exact(connection, metadata + payload, deadline))


def send_frame(connection, frame, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("transport deadline exceeded")
    connection.settimeout(remaining)
    connection.sendall(frame)


def _request_binding(block, request, identity, request_id):
    parameters = asdict(request)
    return {"request_id": request_id, "backend": asdict(identity), "session_id": block.session_id,
            "configuration_id": block.configuration_id, "clock_domain": block.clock_domain,
            "block_sequence": block.sequence, "block_sha256": hashlib.sha256(block.to_wire()).hexdigest(),
            "correlation": parameters, "correlation_sha256": hashlib.sha256(_canonical(parameters)).hexdigest()}


def _result(block, request, block_hash, counts, pairs):
    return {"schema": "orion.lab.pspl-correlation.v1", "session_id": block.session_id,
            "sequence": block.sequence, "block_sha256": block_hash, "request": asdict(request),
            "counts": counts, "pairs": pairs,
            "bin_left_edges_ps": list(range(-request.radius_ps, request.radius_ps + 1, request.bin_width_ps)),
            "boundary": "inclusive radius; floor bins; last bin includes positive endpoint",
            "clock_domain": block.clock_domain, "cross_block_pairs_included": False}


class UnixPLCorrelationWorker:
    """One active request and a pinned producer generation/image; never retries."""
    def __init__(self, socket_path, expected_identity, *, peer_uid, timeout_s=5.0):
        _linux()
        if not isinstance(expected_identity, BackendIdentity):
            raise AcquisitionError("typed backend identity required")
        expected_identity.validate()
        _deadline(timeout_s)
        _integer(peer_uid, "peer uid", 2**32 - 1)
        self.path, self.identity = Path(socket_path), expected_identity
        self.peer_uid, self.timeout_s = peer_uid, timeout_s
        self._flow, self._state_lock = Lock(), RLock()
        self._socket, self._state, self._sequence = None, "created", 0
        self._cancelled = Event()
        self._peer = self._last_exchange = None
        self._completed, self._failed = 0, 0

    def _exchange(self, kind, metadata, payload=b"", *, deadline=None):
        deadline = _deadline(self.timeout_s) if deadline is None else deadline
        if self._cancelled.is_set():
            raise PSPLStateError("transport cancelled")
        if self._socket is None:
            _private_path(self.path, self.peer_uid, existing=True)
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(max(.001, deadline - time.monotonic()))
            with self._state_lock:
                self._socket = connection
            connection.connect(str(self.path))
            self._peer = _peer(connection, self.peer_uid)
            _private_path(self.path, self.peer_uid, existing=True)
        sequence = self._sequence
        self._sequence += 1
        send_frame(self._socket, encode_frame(kind, sequence, metadata, payload), deadline)
        returned, reply_sequence, reply, blob = receive_frame(self._socket, deadline)
        if reply_sequence != sequence or reply.get("request_id") != metadata["request_id"]:
            raise PSPLStateError("reply replay or request mismatch")
        if time.monotonic() >= deadline or self._cancelled.is_set():
            raise PSPLStateError("late or cancelled reply")
        if returned == REJECTED:
            raise PSPLStateError("external backend rejected the request")
        return returned, reply, blob

    def _block(self):
        with self._state_lock:
            self._state = "uncertain"
            self._failed += 1
            connection, self._socket = self._socket, None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _probe(self, *, deadline=None):
        metadata = {"request_id": uuid4().hex, "backend": asdict(self.identity)}
        kind, reply, blob = self._exchange(PROBE, metadata, deadline=deadline)
        if kind != PROBED or blob or _canonical(reply) != _canonical(metadata):
            raise PSPLStateError("backend readback differs from pinned identity")
        with self._state_lock:
            if self._cancelled.is_set():
                raise PSPLStateError("cancelled readback cannot restore worker")
            self._state = "ready"
        return self.snapshot()

    def probe(self):
        if not self._flow.acquire(blocking=False):
            raise PSPLStateError("external worker is busy")
        try:
            if self._state not in {"created", "ready"}:
                raise PSPLStateError("uncertain transport requires explicit new worker/session")
            return self._probe()
        except Exception:
            self._block()
            raise PSPLStateError("external backend probe failed; state uncertain") from None
        finally:
            self._flow.release()

    def correlate(self, block_wire, request):
        block = TimestampBlock.from_wire(block_wire)
        request.validate()
        if len(block.events) > self.identity.max_events:
            raise PSPLStateError("external backend capacity is incompatible")
        if not self._flow.acquire(blocking=False):
            raise PSPLStateError("external worker is busy; retain unsubmitted block")
        started = time.monotonic()
        deadline = _deadline(self.timeout_s)
        try:
            if self._state == "created":
                self._probe(deadline=deadline)
            if self._state != "ready":
                raise PSPLStateError("external transport requires explicit recovery")
            binding = _request_binding(block, request, self.identity, uuid4().hex)
            kind, reply, payload = self._exchange(CORRELATE, binding, block_wire, deadline=deadline)
            required = {**binding, "bin_count": 2 * request.radius_ps // request.bin_width_ps + 1}
            if kind != RESULT or set(reply) != set(required) | {"pairs"}:
                raise PSPLStateError("external result schema differs")
            if _canonical({key: reply[key] for key in required}) != _canonical(required):
                raise PSPLStateError("external result session/configuration/identity differs")
            pairs = _integer(reply["pairs"], "pairs", request.max_pairs)
            if len(payload) != required["bin_count"] * COUNT.size:
                raise PSPLStateError("partial external histogram")
            counts = [item[0] for item in COUNT.iter_unpack(payload)]
            if sum(counts) != pairs or any(value > request.max_pairs for value in counts):
                raise PSPLStateError("external histogram accounting differs")
            with self._state_lock:
                if self._cancelled.is_set() or self._state != "ready":
                    raise PSPLStateError("late result cannot restore cancelled worker")
                self._completed += 1
                self._last_exchange = {"request_id": binding["request_id"], "block_sequence": block.sequence,
                    "block_sha256": binding["block_sha256"], "generation": self.identity.generation,
                    "wall_elapsed_s": time.monotonic() - started, "accepted": True}
            return _result(block, request, binding["block_sha256"], counts, pairs)
        except Exception:
            self._block()
            raise PSPLStateError("external correlation failed; state uncertain; no automatic retry") from None
        finally:
            self._flow.release()

    def snapshot(self):
        with self._state_lock:
            return {"schema": "orion.lab.external-pl-state.v1", "state": self._state,
                    "backend": asdict(self.identity), "peer": self._peer,
                    "completed": self._completed, "failed": self._failed,
                    "last_exchange": dict(self._last_exchange) if self._last_exchange else None,
                    "in_flight": self._flow.locked(), "automatic_retries": 0,
                    "hardware_validated": False, "physical_actuation_locked": True}

    def cancel(self):
        self._cancelled.set()
        self._block()

    def close(self):
        self.cancel()


class PLBackendServer:
    """Single-client CPU/emulation broker with kernel-authenticated local peer.

    A physical plugin needs its own documented UAPI and a fresh ``probe`` method.
    No dynamic import, register operation or port actuation is accepted on wire.
    """
    def __init__(self, socket_path, identity, worker, *, allowed_uid, timeout_s=5.0, state_directory=None):
        _linux()
        if not isinstance(identity, BackendIdentity):
            raise AcquisitionError("typed backend identity required")
        identity.validate()
        if not callable(getattr(worker, "correlate", None)):
            raise AcquisitionError("correlation worker required")
        if identity.mode == "linux-pl-driver" and not callable(getattr(worker, "probe", None)):
            raise PSPLStateError("physical driver needs documented probe/readback interface")
        _deadline(timeout_s)
        _integer(allowed_uid, "client uid", 2**32 - 1)
        self.path, self.identity, self.worker = Path(socket_path), identity, worker
        # The fixture-compatible default is not a production durability claim.
        # The operational CLI must supply persistent state outside /run.
        self.state_directory = Path(state_directory) if state_directory is not None else self.path.parent
        self.allowed_uid, self.timeout_s = allowed_uid, timeout_s
        self._seen, self._sessions = set(), {}
        self._socket = None
        self._generation_fd = self._session_fd = None
        self._session_generations = {}

    def _ledger(self, suffix):
        name = hashlib.sha256(self.identity.backend_id.encode()).hexdigest()[:32]
        path = self.state_directory / f".orion-pl-{name}.{suffix}"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077 or metadata.st_nlink != 1):
            os.close(descriptor)
            raise PSPLStateError("generation ledger ownership/type is unsafe")
        return descriptor

    def _claim_generation(self):
        # Linux flock is process authority, not a DMA/ARM memory barrier.
        import fcntl
        _private_directory(self.state_directory, os.geteuid())
        descriptor = self._ledger("generations")
        self._generation_fd = descriptor
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PSPLStateError("another process owns this backend") from None
        raw = os.read(descriptor, MAX_REPLAY_ENTRIES * COUNT.size + 1)
        if len(raw) % COUNT.size or len(raw) >= MAX_REPLAY_ENTRIES * COUNT.size:
            raise PSPLStateError("generation ledger partial or exhausted")
        generations = [n[0] for n in COUNT.iter_unpack(raw)]
        if generations != sorted(set(generations)) or (generations and self.identity.generation <= generations[-1]):
            raise PSPLStateError("backend generation was already consumed")
        os.lseek(descriptor, 0, os.SEEK_END)
        if os.write(descriptor, COUNT.pack(self.identity.generation)) != COUNT.size:
            raise PSPLStateError("generation ledger write is uncertain")
        os.fsync(descriptor)
        self._session_fd = self._ledger("sessions")
        session_record = struct.Struct("<Q32s")
        records = os.read(self._session_fd, MAX_REPLAY_ENTRIES * session_record.size + 1)
        if len(records) % session_record.size or len(records) > MAX_REPLAY_ENTRIES * session_record.size:
            raise PSPLStateError("session ledger partial or exhausted")
        for generation, digest in session_record.iter_unpack(records):
            if digest in self._session_generations:
                raise PSPLStateError("session ledger has ambiguous ownership")
            self._session_generations[digest] = generation
        # Persist creation/tombstones before exposing a listener or executing.
        directory = os.open(self.state_directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _claim_session(self, session):
        digest = hashlib.sha256(session.encode()).digest()
        previous = self._session_generations.get(digest)
        if previous is not None:
            if previous != self.identity.generation:
                raise PSPLStateError("new backend generation requires a new PS session")
            return
        if len(self._session_generations) >= MAX_REPLAY_ENTRIES:
            raise PSPLStateError("durable session ledger exhausted")
        record = struct.pack("<Q32s", self.identity.generation, digest)
        os.lseek(self._session_fd, 0, os.SEEK_END)
        if os.write(self._session_fd, record) != len(record):
            raise PSPLStateError("session ledger write is uncertain")
        os.fsync(self._session_fd)
        self._session_generations[digest] = self.identity.generation

    def _readback(self):
        if self.identity.mode == "linux-pl-driver":
            actual = BackendIdentity.from_dict(self.worker.probe())
            if actual != self.identity:
                raise PSPLStateError("driver generation/image changed")
        return asdict(self.identity)

    def _handle(self, kind, sequence, meta, payload):
        if not isinstance(meta.get("request_id"), str) or re.fullmatch(r"[0-9a-f]{32}", meta["request_id"]) is None:
            raise PSPLStateError("request identity required")
        if meta["request_id"] in self._seen or len(self._seen) >= MAX_REPLAY_ENTRIES:
            raise PSPLStateError("replay or bounded replay ledger exhausted")
        self._seen.add(meta["request_id"])
        if _canonical({"backend": meta.get("backend")}) != _canonical({"backend": self._readback()}):
            raise PSPLStateError("backend generation/image mismatch")
        if kind == PROBE:
            if set(meta) != {"request_id", "backend"} or payload:
                raise PSPLStateError("invalid readback request")
            return PROBED, meta, b""
        if kind != CORRELATE:
            raise PSPLStateError("unsupported operation")
        block = TimestampBlock.from_wire(payload)
        params = meta.get("correlation")
        if not isinstance(params, dict) or set(params) != set(CorrelationRequest.__dataclass_fields__):
            raise PSPLStateError("invalid correlation request")
        request = CorrelationRequest(**params)
        request.validate()
        if _canonical(meta) != _canonical(_request_binding(block, request, self.identity, meta["request_id"])) or len(block.events) > self.identity.max_events:
            raise PSPLStateError("input/session/configuration binding mismatch")
        previous = self._sessions.get(block.session_id)
        if previous is not None and (block.configuration_id, block.clock_domain, block.sequence) != (
                previous[0], previous[1], previous[2] + 1):
            raise PSPLStateError("replayed or skipped block sequence")
        if previous is not None and block.start_ps < previous[3]:
            raise PSPLStateError("block clock interval regressed")
        if previous is None and len(self._sessions) >= MAX_REPLAY_ENTRIES:
            raise PSPLStateError("bounded session ledger exhausted")
        self._claim_session(block.session_id)
        # Reserve before execution. A lost reply never permits the same block
        # to run again in this producer lifetime; a new session is recovery.
        self._sessions[block.session_id] = (block.configuration_id, block.clock_domain,
                                          block.sequence, block.start_ps + block.duration_ps)
        result = self.worker.correlate(payload, request)
        self._readback()
        counts, pairs = result.get("counts"), result.get("pairs")
        bins = 2 * request.radius_ps // request.bin_width_ps + 1
        if not isinstance(counts, list) or len(counts) != bins or type(pairs) is not int or not 0 <= pairs <= request.max_pairs:
            raise PSPLStateError("worker result geometry invalid")
        if any(type(value) is not int or not 0 <= value <= request.max_pairs for value in counts) or sum(counts) != pairs:
            raise PSPLStateError("worker result accounting invalid")
        if result != _result(block, request, meta["block_sha256"], counts, pairs):
            raise PSPLStateError("worker result source/request binding invalid")
        return RESULT, {**meta, "bin_count": bins, "pairs": pairs}, b"".join(COUNT.pack(value) for value in counts)

    def serve(self, stop_event):
        path = _private_path(self.path, os.geteuid(), existing=False)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = listener
        inode = None
        try:
            self._claim_generation()
            listener.bind(str(path))
            path.chmod(0o600)
            inode = path.lstat().st_ino
            listener.listen(1)
            listener.settimeout(.1)
            while not stop_event.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                with connection:
                    expected = 0
                    try:
                        _peer(connection, self.allowed_uid)
                        while not stop_event.is_set():
                            deadline = _deadline(self.timeout_s)
                            kind, sequence, metadata, payload = receive_frame(connection, deadline)
                            if sequence != expected:
                                raise PSPLStateError("transport replay/order mismatch")
                            expected += 1
                            response, reply, blob = self._handle(kind, sequence, metadata, payload)
                            send_frame(connection, encode_frame(response, sequence, reply, blob), deadline)
                    except Exception:
                        # Close incomplete/rejected exchanges: no unverifiable
                        # error body, automatic retry or fabricated result.
                        pass
        finally:
            listener.close()
            for descriptor in (self._session_fd, self._generation_fd):
                if descriptor is not None:
                    os.close(descriptor)
            self._generation_fd = self._session_fd = None
            if inode is not None and path.is_socket() and path.lstat().st_ino == inode:
                path.unlink()


def probe_linux_character_device(path):
    """Presence/permission report only; never opens a device or guesses its UAPI."""
    _linux()
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or ".." in candidate.parts:
        raise PSPLStateError("absolute nonsymlink character device required")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return {"available": False, "state": "device_missing", "hardware_validated": False}
    if not stat.S_ISCHR(metadata.st_mode) or metadata.st_mode & 0o007:
        raise PSPLStateError("device type/permissions are incompatible")
    return {"available": False, "state": "documented_driver_uapi_required", "hardware_validated": False,
            "device_major": os.major(metadata.st_rdev), "device_minor": os.minor(metadata.st_rdev),
            "device_opened": False, "physical_actuation_locked": True}
