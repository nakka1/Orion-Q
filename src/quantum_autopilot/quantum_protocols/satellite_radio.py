"""Bounded KISS input from an external demodulator; no RF or quantum attestation.

This independently written standard-library implementation uses the wire facts
in Chepponis/Karn's KISS specification (https://www.ka9q.net/papers/kiss.html).
It deliberately quarantines a whole malformed frame rather than retaining bytes
after an invalid escape. Unsupported controls are ignored and counted.

The exact control byte 0x09 is the gr-satellites timestamp extension: one unsigned
64-bit big-endian UNIX timestamp in milliseconds, before a data frame on port 0.
https://gr-satellites.readthedocs.io/en/v5.9.0/command_line.html#kiss-output
It is a reported external reception time, not a synchronized hardware clock.
Supplied captured_at_utc remains separate, and neither is an onboard timestamp.
Demodulation/FEC and mission CRC validation occur outside this KISS interface.

Iterators must be exhausted or closed (e.g. contextlib.closing) to release their
file/socket. Their optional report dictionary records EOF, limits and dropped
partial frames; natural completion also returns it as StopIteration.value.
No partial frame is yielded. Limits include consumer wait time.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import math
from pathlib import Path
import socket
import time


FEND, FESC, TFEND, TFESC = 0xc0, 0xdb, 0xdc, 0xdd
MAX_CHUNK_BYTES = 65536
MAX_DURATION_S = 3600.0
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _positive_integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be an integer between 1 and {maximum}')
    return value


def _capture_time(value):
    if value is None:
        return datetime.now(timezone.utc), None
    original = value.isoformat() if isinstance(value, datetime) else value
    if not isinstance(original, str):
        raise ValueError('captured_at_utc must be an aware UTC datetime or ISO string')
    try:
        instant = datetime.fromisoformat(original.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('captured_at_utc must be an aware UTC datetime or ISO string') from exc
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError('captured_at_utc must explicitly specify UTC')
    return instant.astimezone(timezone.utc), original


class KISSParser:
    """Incremental strict reader; bounded frame and quarantine storage, no queues.

    feed accepts at most 64 KiB per call. A supplied capture time is latched on
    the first byte of a frame and preserved verbatim in captured_at_utc. Without
    one, local_ingress_clock means only when those bytes entered this reader.
    The timestamp extension is single use and discarded across damaged frames,
    unsupported controls or data on another port to prevent timestamp leakage.
    """

    def __init__(self, *, max_frame_bytes=4096, max_quarantine=32):
        self.max_frame_bytes = _positive_integer(max_frame_bytes, 'max_frame_bytes', 1024 * 1024)
        if type(max_quarantine) is not int or not 0 <= max_quarantine <= 256:
            raise ValueError('max_quarantine must be an integer between 0 and 256')
        self._quarantine = deque(maxlen=max_quarantine)
        self._counts = {name: 0 for name in (
            'bytes_received', 'noise_bytes', 'frames_received', 'timestamps_received',
            'unsupported_commands', 'malformed_frames', 'oversized_frames',
            'partial_drops', 'orphan_timestamps', 'quarantined_total')}
        self._started = False
        self._finished = False
        self._termination = None
        self._pending_timestamp = None
        self._reset_frame()

    def _reset_frame(self):
        self._buffer = bytearray()
        self._escaped = False
        self._drop_reason = None
        self._wire_bytes = 0
        self._wire_prefix = bytearray()
        self._wire_hash = hashlib.sha256()
        self._capture = None

    def _forget_timestamp(self):
        if self._pending_timestamp is not None:
            self._counts['orphan_timestamps'] += 1
            self._pending_timestamp = None

    def _quarantine_frame(self, reason):
        self._counts['quarantined_total'] += 1
        self._quarantine.append({'reason': reason, 'wire_bytes': self._wire_bytes,
                                 'wire_prefix_hex': bytes(self._wire_prefix).hex(),
                                 'wire_sha256': self._wire_hash.hexdigest()})
        self._forget_timestamp()

    def _mark_drop(self, reason):
        if self._drop_reason is None:
            self._drop_reason = reason
            self._counts['malformed_frames'] += 1
            if reason == 'oversized_frame':
                self._counts['oversized_frames'] += 1
            self._forget_timestamp()

    def _reject(self, reason):
        self._counts['malformed_frames'] += 1
        self._quarantine_frame(reason)

    def _complete_frame(self):
        control = self._buffer[0]
        payload = bytes(self._buffer[1:])
        if control == 0x09:
            if len(payload) != 8:
                self._reject('invalid_timestamp_length')
                return None
            milliseconds = int.from_bytes(payload, 'big')
            try:
                instant = _EPOCH + timedelta(milliseconds=milliseconds)
            except OverflowError:
                self._reject('timestamp_out_of_range')
                return None
            self._forget_timestamp()
            self._pending_timestamp = (instant, milliseconds)
            self._counts['timestamps_received'] += 1
            return None
        if control & 0x0f:
            self._counts['unsupported_commands'] += 1
            self._forget_timestamp()
            return None
        if not payload:
            self._reject('empty_data_frame')
            return None
        if len(payload) > self.max_frame_bytes:
            self._counts['oversized_frames'] += 1
            self._reject('oversized_frame')
            return None
        port = control >> 4
        capture, provided = self._capture
        pending = self._pending_timestamp
        if port != 0:
            self._forget_timestamp()
            pending = None
        self._pending_timestamp = None
        self._counts['frames_received'] += 1
        return {'frame_bytes': payload, 'kiss_port': port,
                'received_at_utc': (pending[0] if pending else capture).isoformat(),
                'timestamp_origin': ('kiss_timestamp_extension' if pending else
                                     'supplied_capture_time' if provided is not None else 'local_ingress_clock'),
                'captured_at_utc': provided,
                'kiss_timestamp_unix_ms': pending[1] if pending else None,
                'timestamp_qualified': False}

    def feed(self, data: bytes, *, captured_at_utc=None) -> list[dict]:
        if self._finished:
            raise ValueError('KISSParser is finished')
        if not isinstance(data, bytes) or len(data) > MAX_CHUNK_BYTES:
            raise ValueError('data must be immutable bytes of at most 65536 bytes')
        capture = _capture_time(captured_at_utc)
        self._counts['bytes_received'] += len(data)
        records = []
        for byte in data:
            if byte == FEND:
                if self._started and self._wire_bytes:
                    if self._escaped:
                        self._mark_drop('incomplete_escape')
                    if self._drop_reason:
                        self._quarantine_frame(self._drop_reason)
                    elif self._buffer:
                        record = self._complete_frame()
                        if record is not None:
                            records.append(record)
                self._started = True
                self._reset_frame()
                continue
            if not self._started:
                self._counts['noise_bytes'] += 1
                continue
            if self._capture is None:
                self._capture = capture
            self._wire_bytes += 1
            self._wire_hash.update(bytes((byte,)))
            if len(self._wire_prefix) < 64:
                self._wire_prefix.append(byte)
            if self._drop_reason:
                continue
            if self._escaped:
                self._escaped = False
                if byte == TFEND:
                    byte = FEND
                elif byte == TFESC:
                    byte = FESC
                else:
                    self._mark_drop('invalid_escape')
                    continue
            elif byte == FESC:
                self._escaped = True
                continue
            self._buffer.append(byte)
            # The timestamp extension needs nine bytes even with a small data limit.
            if len(self._buffer) > max(self.max_frame_bytes + 1, 9):
                self._mark_drop('oversized_frame')
                self._buffer.clear()
        return records

    def finish(self, reason='eof'):
        if self._finished:
            return
        if not isinstance(reason, str) or not reason:
            raise ValueError('A termination reason is required')
        if self._wire_bytes:
            self._counts['partial_drops'] += 1
            self._quarantine_frame(f'partial_frame_at_{reason}')
        self._forget_timestamp()
        self._reset_frame()
        self._finished = True
        self._termination = reason

    def snapshot(self) -> dict:
        return {**self._counts, 'buffered_bytes': len(self._buffer),
                'quarantine': [dict(record) for record in self._quarantine],
                'quarantine_events_omitted': self._counts['quarantined_total'] - len(self._quarantine),
                'finished': self._finished, 'termination': self._termination}


def _options(max_frames, timeout_s, max_bytes, chunk_bytes, cancel, report, parser):
    _positive_integer(max_frames, 'max_frames', 1000000)
    _positive_integer(max_bytes, 'max_bytes', MAX_TOTAL_BYTES)
    _positive_integer(chunk_bytes, 'chunk_bytes', MAX_CHUNK_BYTES)
    if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= MAX_DURATION_S):
        raise ValueError('timeout_s must be finite, positive and at most 3600 seconds')
    if cancel is None:
        def cancelled():
            return False
    elif callable(cancel):
        cancelled = cancel
    elif callable(getattr(cancel, 'is_set', None)):
        cancelled = cancel.is_set
    else:
        raise ValueError('cancel must be callable or expose is_set()')
    if report is not None and not isinstance(report, dict):
        raise ValueError('report must be a dictionary')
    if parser is not None and not isinstance(parser, KISSParser):
        raise ValueError('parser must be a KISSParser')
    active = parser if parser is not None else KISSParser()
    if active.snapshot()['finished']:
        raise ValueError('parser is already finished')
    status = report if report is not None else {}
    status.clear()
    status.update({'termination': 'pending', 'frames_returned': 0, 'bytes_read': 0,
                   'frames_received_before': active.snapshot()['frames_received']})
    started = time.monotonic()
    return active, status, cancelled, started, started + timeout_s


def _stop(cancelled, deadline):
    if cancelled():
        return 'cancelled'
    if time.monotonic() >= deadline:
        return 'deadline'
    return None


def _finish(active, status, started):
    if status['termination'] == 'pending':
        status['termination'] = 'closed'
    active.finish(status['termination'])
    snapshot = active.snapshot()
    status.update({'elapsed_s': time.monotonic() - started, 'parser': snapshot,
                   'frames_not_returned': (snapshot['frames_received'] - status['frames_received_before']
                                           - status['frames_returned'])})


def iter_kiss_file(path, *, max_frames: int, timeout_s: float,
                   max_bytes=16 * 1024 * 1024, chunk_bytes=4096,
                   captured_at_utc=None, cancel=None, report=None, parser=None):
    """Yield complete data frames; report distinguishes EOF from every limit.

    A timestamp-free file uses local_ingress_clock unless captured_at_utc was
    supplied. File modification time is never substituted for reception time.
    """
    active, status, cancelled, started, deadline = _options(
        max_frames, timeout_s, max_bytes, chunk_bytes, cancel, report, parser)
    _capture_time(captured_at_utc)
    status['source_type'] = 'kiss_file'
    try:
        with Path(path).open('rb') as stream:
            while True:
                status['termination'] = _stop(cancelled, deadline) or 'pending'
                if status['termination'] != 'pending':
                    break
                remaining = max_bytes - status['bytes_read']
                if remaining <= 0:
                    status['termination'] = 'byte_limit'
                    break
                data = stream.read(min(chunk_bytes, remaining))
                if not data:
                    status['termination'] = 'eof'
                    break
                status['bytes_read'] += len(data)
                for record in active.feed(data, captured_at_utc=captured_at_utc):
                    reason = _stop(cancelled, deadline)
                    if reason:
                        status['termination'] = reason
                        break
                    status['frames_returned'] += 1
                    yield record
                    if status['frames_returned'] >= max_frames:
                        status['termination'] = 'max_frames'
                        break
                if status['termination'] != 'pending':
                    break
    except OSError as exc:
        status.update({'termination': 'file_error', 'error': type(exc).__name__})
    finally:
        _finish(active, status, started)
    return status


def _loopback_address(host):
    if not isinstance(host, str):
        raise ValueError('host must be a literal loopback IP address or localhost')
    if host == 'localhost':
        return '127.0.0.1'
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError) as exc:
        raise ValueError('host must be a literal loopback IP address or localhost') from exc
    if not address.is_loopback:
        raise ValueError('Only loopback KISS TCP receivers are supported')
    return str(address)


def receive_kiss_tcp(host, port, *, max_frames: int, timeout_s: float,
                     max_bytes=16 * 1024 * 1024, chunk_bytes=4096,
                     captured_at_utc=None, cancel=None, report=None, parser=None):
    """Read a local external demodulator, without sending commands or reconnecting.

    A deadline is mandatory and at most one hour. Socket waits poll cancellation
    at most every 100 ms. TCP EOF and errors are explicit in report; disconnect
    drops any incomplete KISS frame. No socket is opened outside loopback.
    """
    address = _loopback_address(host)
    _positive_integer(port, 'port', 65535)
    active, status, cancelled, started, deadline = _options(
        max_frames, timeout_s, max_bytes, chunk_bytes, cancel, report, parser)
    _capture_time(captured_at_utc)
    status['source_type'] = 'loopback_kiss_tcp'
    try:
        reason = _stop(cancelled, deadline)
        if reason:
            status['termination'] = reason
            return status
        try:
            stream = socket.create_connection((address, port),
                                               timeout=min(0.1, max(0.000001, deadline - time.monotonic())))
        except OSError as exc:
            status.update({'termination': _stop(cancelled, deadline) or 'connect_error',
                           'error': type(exc).__name__})
            return status
        with stream:
            while True:
                status['termination'] = _stop(cancelled, deadline) or 'pending'
                if status['termination'] != 'pending':
                    break
                remaining = max_bytes - status['bytes_read']
                if remaining <= 0:
                    status['termination'] = 'byte_limit'
                    break
                try:
                    stream.settimeout(min(0.1, max(0.000001, deadline - time.monotonic())))
                    data = stream.recv(min(chunk_bytes, remaining))
                except socket.timeout:
                    continue
                except OSError as exc:
                    status.update({'termination': 'socket_error', 'error': type(exc).__name__})
                    break
                if not data:
                    status['termination'] = 'eof'
                    break
                status['bytes_read'] += len(data)
                for record in active.feed(data, captured_at_utc=captured_at_utc):
                    reason = _stop(cancelled, deadline)
                    if reason:
                        status['termination'] = reason
                        break
                    status['frames_returned'] += 1
                    yield record
                    if status['frames_returned'] >= max_frames:
                        status['termination'] = 'max_frames'
                        break
                if status['termination'] != 'pending':
                    break
    except OSError as exc:
        status.update({'termination': 'socket_error', 'error': type(exc).__name__})
    finally:
        _finish(active, status, started)
    return status
