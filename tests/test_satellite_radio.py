"""KISS wire cases and real local sockets; no SDR/RF is involved in these tests."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest

from quantum_autopilot.quantum_protocols import satellite_radio as radio
from quantum_autopilot.quantum_protocols.satellite_missions import (
    CSP1_BEACON0_PROFILE, SATNOGS_BEACON0_PROFILE, decode_frame,
)


pytestmark = pytest.mark.unit
CAPTURE = '2026-10-08T13:55:20Z'
WIRE_TIME_MS = 1791467720000
WIRE_TIME = '2026-10-08T13:55:20+00:00'
FIXTURES = Path(__file__).parent / 'fixtures' / 'satellite_public'


def kiss(control, payload):
    # Independent wire construction; the known literal escaping case is checked below.
    body = bytes((control,)) + payload
    return b'\xc0' + body.replace(b'\xdb', b'\xdb\xdd').replace(b'\xc0', b'\xdb\xdc') + b'\xc0'


def timestamp(milliseconds=WIRE_TIME_MS):
    return kiss(0x09, milliseconds.to_bytes(8, 'big'))


ESCAPED = b'\xc0\x00\x01\xdb\xdc\x02\xdb\xdd\x03\xc0'
EXPECTED = b'\x01\xc0\x02\xdb\x03'


def test_known_escaping_noise_idle_and_capture_time_preservation():
    parser = radio.KISSParser()
    assert kiss(0, EXPECTED) == ESCAPED
    records = parser.feed(b'noise\xc0\xc0' + ESCAPED + b'\xc0', captured_at_utc=CAPTURE)
    assert len(records) == 1
    assert records[0] == {'frame_bytes': EXPECTED, 'kiss_port': 0,
                           'received_at_utc': WIRE_TIME, 'timestamp_origin': 'supplied_capture_time',
                           'captured_at_utc': CAPTURE, 'kiss_timestamp_unix_ms': None,
                           'timestamp_qualified': False}
    parser.finish()
    state = parser.snapshot()
    assert state['noise_bytes'] == 5 and state['frames_received'] == 1
    assert state['malformed_frames'] == state['partial_drops'] == 0
    assert state['termination'] == 'eof' and state['buffered_bytes'] == 0
    assert 'origin' not in records[0] and 'qber' not in records[0]


@pytest.mark.parametrize('split', range(len(timestamp() + ESCAPED) + 1))
def test_every_single_split_preserves_timestamp_and_escaped_payload(split):
    data = timestamp() + ESCAPED
    parser = radio.KISSParser()
    records = parser.feed(data[:split], captured_at_utc=CAPTURE)
    records += parser.feed(data[split:], captured_at_utc=CAPTURE)
    parser.finish()
    assert len(records) == 1 and records[0]['frame_bytes'] == EXPECTED
    assert records[0]['received_at_utc'] == WIRE_TIME
    assert records[0]['timestamp_origin'] == 'kiss_timestamp_extension'
    assert records[0]['kiss_timestamp_unix_ms'] == WIRE_TIME_MS
    assert records[0]['captured_at_utc'] == CAPTURE and records[0]['timestamp_qualified'] is False
    assert parser.snapshot()['timestamps_received'] == 1
    assert parser.snapshot()['orphan_timestamps'] == 0


def test_byte_at_a_time_and_timestamp_single_use():
    parser = radio.KISSParser()
    records = []
    for byte in timestamp() + ESCAPED + kiss(0x20, b'port2') + kiss(0, b'untimed'):
        records.extend(parser.feed(bytes((byte,)), captured_at_utc='2026-10-08T14:01:00Z'))
    assert [record['frame_bytes'] for record in records] == [EXPECTED, b'port2', b'untimed']
    assert [record['kiss_port'] for record in records] == [0, 2, 0]
    assert [record['timestamp_origin'] for record in records] == [
        'kiss_timestamp_extension', 'supplied_capture_time', 'supplied_capture_time']
    assert records[0]['received_at_utc'] == WIRE_TIME
    assert records[0]['captured_at_utc'] == '2026-10-08T14:01:00Z'
    assert records[1]['received_at_utc'] == records[2]['received_at_utc'] == '2026-10-08T14:01:00+00:00'


def test_capture_time_is_latched_on_first_body_byte_across_chunks():
    parser = radio.KISSParser()
    assert parser.feed(b'\xc0', captured_at_utc='2026-10-08T13:00:00Z') == []
    assert parser.feed(b'\x00A', captured_at_utc=CAPTURE) == []
    records = parser.feed(b'B\xc0', captured_at_utc='2026-10-08T14:00:00Z')
    assert records[0]['captured_at_utc'] == CAPTURE and records[0]['received_at_utc'] == WIRE_TIME


def test_no_capture_uses_explicit_local_ingress_clock_not_file_or_onboard_time():
    before = datetime.now(timezone.utc)
    record = radio.KISSParser().feed(kiss(0, b'A'))[0]
    after = datetime.now(timezone.utc)
    assert before <= datetime.fromisoformat(record['received_at_utc']) <= after
    assert record['timestamp_origin'] == 'local_ingress_clock'
    assert record['captured_at_utc'] is None and record['timestamp_qualified'] is False


@pytest.mark.parametrize('milliseconds,expected', (
    (0, '1970-01-01T00:00:00+00:00'),
    (1, '1970-01-01T00:00:00.001000+00:00'),
    (2012239320000, '2033-10-06T19:22:00+00:00'),
))
def test_timestamp_zero_milliseconds_and_future_are_preserved_unqualified(milliseconds, expected):
    record = radio.KISSParser().feed(timestamp(milliseconds) + kiss(0, b'A'), captured_at_utc=CAPTURE)[0]
    assert record['received_at_utc'] == expected
    assert record['kiss_timestamp_unix_ms'] == milliseconds and record['timestamp_qualified'] is False
    assert record['captured_at_utc'] == CAPTURE


@pytest.mark.parametrize('bad,reason', (
    (b'\xc0\x00A\xdbxB\xc0', 'invalid_escape'),
    (b'\xc0\x00A\xdb\xc0', 'incomplete_escape'),
    (kiss(0, b''), 'empty_data_frame'),
    (kiss(9, b''), 'invalid_timestamp_length'),
    (kiss(9, b'1234567'), 'invalid_timestamp_length'),
    (timestamp(2**64 - 1), 'timestamp_out_of_range'),
))
def test_malformed_frames_quarantined_whole_and_recovery_drops_stale_timestamp(bad, reason):
    parser = radio.KISSParser()
    records = parser.feed(timestamp() + bad + kiss(0, b'good'), captured_at_utc=CAPTURE)
    assert [record['frame_bytes'] for record in records] == [b'good']
    assert records[0]['timestamp_origin'] == 'supplied_capture_time'
    state = parser.snapshot()
    assert state['malformed_frames'] == state['quarantined_total'] == 1
    assert state['orphan_timestamps'] == 1 and state['quarantine'][0]['reason'] == reason


@pytest.mark.parametrize('control', (0x01, 0x06, 0x19, 0xff))
def test_unsupported_controls_ignored_counted_and_not_guessed_as_timestamps(control):
    parser = radio.KISSParser()
    records = parser.feed(timestamp() + kiss(control, b'12345678') + kiss(0, b'A'), captured_at_utc=CAPTURE)
    assert records[0]['timestamp_origin'] == 'supplied_capture_time'
    state = parser.snapshot()
    assert state['unsupported_commands'] == 1 and state['timestamps_received'] == 1
    assert state['orphan_timestamps'] == 1 and state['malformed_frames'] == 0


def test_other_port_discards_timestamp_and_repeated_timestamp_counts_orphan():
    parser = radio.KISSParser()
    records = parser.feed(timestamp(0) + timestamp() + kiss(0x10, b'A') + kiss(0, b'B'),
                          captured_at_utc=CAPTURE)
    assert [record['timestamp_origin'] for record in records] == ['supplied_capture_time'] * 2
    assert parser.snapshot()['orphan_timestamps'] == 2
    parser.feed(timestamp())
    parser.finish()
    assert parser.snapshot()['orphan_timestamps'] == 3


@pytest.mark.parametrize('partial', (b'\xc0\x00A', b'\xc0\x00A\xdb', b'\xc0\x09\x00'))
def test_eof_partial_is_never_yielded_and_finish_is_idempotent(partial):
    parser = radio.KISSParser()
    assert parser.feed(partial) == []
    parser.finish('eof')
    state = parser.snapshot()
    assert state['partial_drops'] == 1 and state['quarantined_total'] == 1
    assert state['quarantine'][0]['reason'] == 'partial_frame_at_eof'
    parser.finish('closed')
    assert parser.snapshot() == state
    with pytest.raises(ValueError, match='finished'):
        parser.feed(kiss(0, b'A'))


def test_frame_and_quarantine_bounds_while_stream_is_malformed():
    parser = radio.KISSParser(max_frame_bytes=16, max_quarantine=2)
    parser.feed(b'\xc0\x00' + b'A' * 60000)
    assert parser.snapshot()['buffered_bytes'] <= 17
    parser.feed(b'A' * 60000 + b'\xc0')
    parser.feed(kiss(0, b'') * 4)
    records = parser.feed(kiss(0, b'OK'))
    state = parser.snapshot()
    assert records[0]['frame_bytes'] == b'OK'
    assert state['oversized_frames'] == 1 and state['malformed_frames'] == 5
    assert state['quarantined_total'] == 5 and len(state['quarantine']) == 2
    assert state['quarantine_events_omitted'] == 3
    assert all(len(record['wire_prefix_hex']) <= 128 for record in state['quarantine'])
    state['quarantine'][0]['reason'] = 'caller mutation'
    assert parser.snapshot()['quarantine'][0]['reason'] == 'empty_data_frame'


def test_small_frame_limit_still_allows_timestamp_but_rejects_oversized_data():
    parser = radio.KISSParser(max_frame_bytes=1, max_quarantine=0)
    records = parser.feed(timestamp() + kiss(0, b'AB') + kiss(0, b'A'), captured_at_utc=CAPTURE)
    assert len(records) == 1 and records[0]['frame_bytes'] == b'A'
    state = parser.snapshot()
    assert state['oversized_frames'] == 1 and state['quarantine'] == []
    assert state['quarantine_events_omitted'] == 1 and state['orphan_timestamps'] == 1


@pytest.mark.parametrize('capture', ('2026-10-08T13:55:20', 'bad', '2026-10-08T13:55:20-03:00', 123))
def test_invalid_or_non_utc_capture_timestamp_is_rejected_before_bytes(capture):
    parser = radio.KISSParser()
    with pytest.raises(ValueError, match='captured_at_utc'):
        parser.feed(kiss(0, b'A'), captured_at_utc=capture)
    assert parser.snapshot()['bytes_received'] == 0


@pytest.mark.parametrize('data', (bytearray(b'A'), 'A', None, b'A' * 65537),
                         ids=('mutable', 'string', 'none', 'oversized'))
def test_feed_rejects_mutable_nonbytes_and_unbounded_chunk(data):
    parser = radio.KISSParser()
    with pytest.raises(ValueError):
        parser.feed(data)
    assert parser.snapshot()['bytes_received'] == 0


@pytest.mark.parametrize('options', ({'max_frame_bytes': 0}, {'max_frame_bytes': True},
                                    {'max_quarantine': -1}, {'max_quarantine': 257}))
def test_parser_limits_must_be_bounded_positive_integers(options):
    with pytest.raises(ValueError):
        radio.KISSParser(**options)


def test_file_eof_and_partial_drop_are_explicit(tmp_path):
    path = tmp_path / 'capture.kiss'
    data = timestamp() + ESCAPED + kiss(0, b'B') + b'\xc0\x00partial'
    path.write_bytes(data)
    report = {}
    records = list(radio.iter_kiss_file(path, max_frames=10, timeout_s=1, chunk_bytes=1,
                                       captured_at_utc=CAPTURE, report=report))
    assert [record['frame_bytes'] for record in records] == [EXPECTED, b'B']
    assert records[0]['timestamp_origin'] == 'kiss_timestamp_extension'
    assert records[1]['captured_at_utc'] == CAPTURE
    assert report['termination'] == 'eof' and report['bytes_read'] == len(data)
    assert report['frames_returned'] == 2 and report['parser']['partial_drops'] == 1
    assert report['parser']['finished'] is True and report['frames_not_returned'] == 0
    assert path.read_bytes() == data


def test_file_frame_limit_reports_complete_unreturned_frames(tmp_path):
    path = tmp_path / 'capture.kiss'
    path.write_bytes(kiss(0, b'A') + kiss(0, b'B'))
    report = {}
    records = list(radio.iter_kiss_file(path, max_frames=1, timeout_s=1, report=report))
    assert [record['frame_bytes'] for record in records] == [b'A']
    assert report['termination'] == 'max_frames' and report['frames_not_returned'] == 1


def test_file_byte_limit_reports_truncated_input(tmp_path):
    path = tmp_path / 'capture.kiss'
    path.write_bytes(kiss(0, b'ABC'))
    report = {}
    assert list(radio.iter_kiss_file(path, max_frames=2, timeout_s=1, max_bytes=4, report=report)) == []
    assert report['termination'] == 'byte_limit' and report['bytes_read'] == 4
    assert report['parser']['partial_drops'] == 1


def test_file_cancel_deadline_and_early_close_are_explicit(tmp_path, monkeypatch):
    path = tmp_path / 'capture.kiss'
    path.write_bytes(kiss(0, b'A') + kiss(0, b'B'))
    cancelled = threading.Event()
    cancelled.set()
    report = {}
    assert list(radio.iter_kiss_file(path, max_frames=2, timeout_s=1, cancel=cancelled, report=report)) == []
    assert report['termination'] == 'cancelled' and report['bytes_read'] == 0
    clock = [10.0]
    monkeypatch.setattr(radio, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    stream = radio.iter_kiss_file(path, max_frames=10, timeout_s=1, chunk_bytes=4, report=report)
    assert next(stream)['frame_bytes'] == b'A'
    clock[0] += 2
    assert list(stream) == [] and report['termination'] == 'deadline'
    assert report['elapsed_s'] == 2
    stream = radio.iter_kiss_file(path, max_frames=10, timeout_s=1, chunk_bytes=4, report=report)
    assert next(stream)['frame_bytes'] == b'A'
    stream.close()
    assert report['termination'] == 'closed' and report['parser']['finished'] is True


def test_missing_file_is_explicit_file_error(tmp_path):
    report = {}
    assert list(radio.iter_kiss_file(tmp_path / 'missing', max_frames=1, timeout_s=1, report=report)) == []
    assert report['termination'] == 'file_error' and report['error'] == 'FileNotFoundError'


def test_iterator_returns_eof_report_even_without_output_dictionary(tmp_path):
    path = tmp_path / 'empty.kiss'
    path.write_bytes(b'')
    stream = radio.iter_kiss_file(path, max_frames=1, timeout_s=1)
    with pytest.raises(StopIteration) as stopped:
        next(stream)
    assert stopped.value.value['termination'] == 'eof'
    assert stopped.value.value['parser']['finished'] is True


@pytest.mark.parametrize('options', ({'max_frames': 0}, {'max_frames': True}, {'timeout_s': 0},
                                    {'timeout_s': float('nan')}, {'timeout_s': float('inf')},
                                    {'timeout_s': 3601}, {'max_bytes': 0}, {'chunk_bytes': 65537},
                                    {'cancel': 'invalid'}, {'report': []}))
def test_iterator_limits_are_mandatory_and_bounded(tmp_path, options):
    kwargs = {'max_frames': 1, 'timeout_s': 1, **options}
    with pytest.raises(ValueError):
        list(radio.iter_kiss_file(tmp_path / 'missing', **kwargs))


@contextmanager
def local_server(chunks, *, hold=False, accepted=None):
    """Only the test server sends bytes; worker cleanup is checked after each test."""
    release = threading.Event()
    errors = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]

    def worker():
        try:
            with listener:
                connection, _peer = listener.accept()
                with connection:
                    if accepted is not None:
                        accepted.set()
                    for chunk in chunks:
                        connection.sendall(chunk)
                    if hold:
                        release.wait(2)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name='test-local-kiss-source', daemon=True)
    thread.start()
    try:
        yield port
    finally:
        release.set()
        thread.join(3)
        listener.close()
        assert not thread.is_alive(), 'local test server did not terminate'
        assert errors == []


def test_real_local_socket_split_escapes_timestamp_and_disconnect():
    data = timestamp() + ESCAPED + b'\xc0\x00truncated\xdb'
    chunks = [data[:3], data[3:7], data[7:15], data[15:19], data[19:]]
    report = {}
    with local_server(chunks) as port:
        records = list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=10, timeout_s=1,
                                               chunk_bytes=1, report=report))
    assert [record['frame_bytes'] for record in records] == [EXPECTED]
    assert records[0]['received_at_utc'] == WIRE_TIME
    assert records[0]['timestamp_origin'] == 'kiss_timestamp_extension'
    assert records[0]['captured_at_utc'] is None and records[0]['timestamp_qualified'] is False
    assert report['source_type'] == 'loopback_kiss_tcp' and report['termination'] == 'eof'
    assert report['parser']['partial_drops'] == 1 and report['bytes_read'] == len(data)


def test_real_local_socket_deadline_keeps_complete_frame_and_drops_partial():
    report = {}
    with local_server([kiss(0, b'A') + b'\xc0\x00partial'], hold=True) as port:
        records = list(radio.receive_kiss_tcp('localhost', port, max_frames=10, timeout_s=0.12,
                                               report=report))
    assert [record['frame_bytes'] for record in records] == [b'A']
    assert records[0]['timestamp_origin'] == 'local_ingress_clock'
    assert report['termination'] == 'deadline' and report['parser']['partial_drops'] == 1
    assert report['elapsed_s'] < 1


def test_real_local_socket_cancel_is_polled_and_frame_limit_is_explicit():
    report = {}
    cancelled = threading.Event()
    with local_server([], hold=True, accepted=cancelled) as port:
        assert list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=1, timeout_s=1,
                                          cancel=cancelled, report=report)) == []
    assert report['termination'] == 'cancelled' and report['elapsed_s'] < 1
    with local_server([kiss(0, b'A') + kiss(0, b'B')], hold=True) as port:
        records = list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=1, timeout_s=1,
                                               report=report))
    assert [record['frame_bytes'] for record in records] == [b'A']
    assert report['termination'] == 'max_frames' and report['frames_not_returned'] == 1


def test_real_local_socket_byte_limit_and_supplied_capture_are_explicit():
    report = {}
    with local_server([kiss(0, b'A') + kiss(0, b'partial')], hold=True) as port:
        records = list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=10, timeout_s=1,
                                               max_bytes=8, captured_at_utc=CAPTURE, report=report))
    assert [record['frame_bytes'] for record in records] == [b'A']
    assert records[0]['captured_at_utc'] == CAPTURE
    assert records[0]['timestamp_origin'] == 'supplied_capture_time'
    assert report['termination'] == 'byte_limit' and report['parser']['partial_drops'] == 1


def test_real_local_socket_early_iterator_close_releases_connection():
    report = {}
    with local_server([kiss(0, b'A') + b'\xc0\x00partial'], hold=True) as port:
        stream = radio.receive_kiss_tcp('127.0.0.1', port, max_frames=10, timeout_s=1,
                                        chunk_bytes=1, report=report)
        assert next(stream)['frame_bytes'] == b'A'
        stream.close()
    assert report['termination'] == 'closed'
    assert report['parser']['finished'] is True and report['parser']['partial_drops'] == 0


def test_socket_error_is_distinct_from_eof_and_drops_partial(monkeypatch):
    class BrokenStream:
        calls = 0
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def settimeout(self, timeout):
            assert 0 < timeout <= 0.1

        def recv(self, length):
            self.calls += 1
            if self.calls == 1:
                return b'\xc0\x00partial'
            raise ConnectionResetError('test reset')

    broken = BrokenStream()
    monkeypatch.setattr(radio.socket, 'create_connection', lambda *args, **kwargs: broken)
    report = {}
    assert list(radio.receive_kiss_tcp('127.0.0.1', 8100, max_frames=1, timeout_s=1, report=report)) == []
    assert report['termination'] == 'socket_error' and report['error'] == 'ConnectionResetError'
    assert report['parser']['partial_drops'] == 1 and broken.closed is True


def test_connection_refused_and_pre_cancel_are_explicit_without_retry():
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]  # Bound but deliberately not listening.
        report = {}
        assert list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=1, timeout_s=1, report=report)) == []
    assert report['termination'] == 'connect_error' and report['parser']['finished'] is True
    cancelled = threading.Event()
    cancelled.set()
    report = {}
    assert list(radio.receive_kiss_tcp('127.0.0.1', port, max_frames=1, timeout_s=1,
                                      cancel=cancelled, report=report)) == []
    assert report['termination'] == 'cancelled' and report['bytes_read'] == 0


@pytest.mark.parametrize('host', ('8.8.8.8', '0.0.0.0', 'example.com', '::', '', None, 2130706433))
def test_non_loopback_and_dns_targets_rejected_before_socket(host, monkeypatch):
    calls = []
    monkeypatch.setattr(radio.socket, 'create_connection', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        list(radio.receive_kiss_tcp(host, 8100, max_frames=1, timeout_s=1))
    assert calls == []


@pytest.mark.parametrize('profile,length', ((SATNOGS_BEACON0_PROFILE, 96), (CSP1_BEACON0_PROFILE, 92)))
def test_kiss_file_preserves_real_fixture_bytes_and_uses_selected_mission_decoder(tmp_path, profile, length):
    captured = (FIXTURES / 'suomi100-frame-20261008-135520.bin').read_bytes()
    wire_frame = captured[:length]  # 92 is explicitly a test derivative, not a claimed RF capture.
    path = tmp_path / 'suomi.kiss'
    path.write_bytes(timestamp() + kiss(0, wire_frame))
    report = {}
    records = list(radio.iter_kiss_file(path, max_frames=2, timeout_s=1, report=report))
    assert len(records) == 1 and records[0]['frame_bytes'] == wire_frame
    result = decode_frame('suomi100', records[0]['frame_bytes'], profile=profile)
    assert result['payload']['eps']['batt_v'] == 7285 and result['integrity']['verified'] is True
    assert result['clock']['onboard_timestamps']['eps']['interpreted_utc'] == '2033-10-06T19:22:00+00:00'
    assert result['qualification']['housekeeping_validated'] is False
    assert result['qualification']['ogs_validated'] is False and result['qualification']['qkd_validated'] is False
    assert records[0]['received_at_utc'] == WIRE_TIME and report['termination'] == 'eof'
    assert (FIXTURES / 'suomi100-frame-20261008-135520.bin').read_bytes() == captured
