"""Raw evidence batching preserves deep-copy API, hashes, quota and durability."""
import hashlib
import json
import zlib

import pytest

from quantum_autopilot.control_plane.sqlite_transaction import connect
from quantum_autopilot.quantum_protocols.contracts import Acquisition, ProtocolConfig, ProtocolError, canonical
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime, Session

pytestmark=pytest.mark.unit


def setup_runtime(tmp_path,limit=2*1024**3):
    runtime=ProtocolRuntime(tmp_path/'evidence.sqlite','evidence-test')
    session=Session(ProtocolConfig('storage',('satellite_isl',),('a','b'),('a','b'),('ab',),
        parameters={'raw_store_limit_bytes':limit}))
    return runtime,session


def evidence(label):
    serialized=canonical({'schema':'storage-test','identity':label,'raw':{'time_ps':list(range(120))}})
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest(),serialized


def count_rows(runtime):
    with connect(runtime.journal_path) as conn:
        return tuple(conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
            for table in ('satellite_acquisitions','technology_events'))


def test_fast_json_matches_public_deep_copy_and_exposes_no_alias():
    acquisition=Acquisition('c','s','isl_block','fixture','simulated',1,0.,0.,('q',),('c',),
        {'tdc':[{'time_ps':[10,20],'detector':[0,1]}]})
    assert acquisition.canonical_json()==canonical(acquisition.to_dict())
    exported=acquisition.to_dict();exported['raw']['tdc'][0]['time_ps'][0]=999
    decoded=json.loads(acquisition.canonical_json());decoded['raw']['tdc'].clear()
    assert acquisition.raw['tdc'][0]['time_ps']==[10,20]


def test_pending_quota_rejects_second_blob_without_losing_first(tmp_path):
    a,b=evidence('first'),evidence('second')
    size=len(zlib.compress(a[1].encode('utf-8')))
    runtime,session=setup_runtime(tmp_path,size)
    runtime._queue_raw_acquisition(session,*a)
    runtime._event(session,0.,'acquired',{'observation_sha256':a[0]})
    assert count_rows(runtime)==(0,0)
    with pytest.raises(ProtocolError) as error:runtime._queue_raw_acquisition(session,*b)
    assert error.value.code=='storage_full' and list(runtime._raw_outbox)==[a[0]]
    runtime._flush();assert count_rows(runtime)==(1,1)
    runtime._queue_raw_acquisition(session,*a)
    assert not runtime._raw_outbox  # Persisted identical evidence costs no extra quota.


def test_batches_deduplicate_and_commit_receipts_with_exact_evidence(tmp_path):
    runtime,session=setup_runtime(tmp_path)
    records=[evidence('first'),evidence('second')]
    for record in records:
        runtime._queue_raw_acquisition(session,*record)
        runtime._queue_raw_acquisition(session,*record)
        runtime._event(session,0.,'acquired',{'observation_sha256':record[0]})
    runtime._flush();assert count_rows(runtime)==(2,2)
    with connect(runtime.journal_path) as conn:
        for sha,blob in conn.execute('SELECT sha256,observation_zlib FROM satellite_acquisitions'):
            serialized=zlib.decompress(blob)
            assert hashlib.sha256(serialized).hexdigest()==sha
        refs=[json.loads(row[0])['detail']['observation_sha256'] for row in conn.execute('SELECT payload_json FROM technology_events')]
    assert set(refs)=={record[0] for record in records} and not runtime._outbox and not runtime._raw_outbox


def test_flush_failure_rolls_back_blobs_and_receipts_and_keeps_pending(tmp_path):
    runtime,session=setup_runtime(tmp_path)
    record=evidence('first');runtime._queue_raw_acquisition(session,*record)
    runtime._event(session,0.,'acquired',{'observation_sha256':record[0]})
    with connect(runtime.journal_path) as conn:
        conn.execute("CREATE TRIGGER refuse_receipt BEFORE INSERT ON technology_events BEGIN SELECT RAISE(ABORT,'forced rollback'); END")
    with pytest.raises(Exception,match='forced rollback'):runtime._flush()
    assert count_rows(runtime)==(0,0) and runtime._outbox and runtime._raw_outbox
    with connect(runtime.journal_path) as conn:conn.execute('DROP TRIGGER refuse_receipt')
    runtime._flush();assert count_rows(runtime)==(1,1)


def test_final_quota_checks_other_writer_atomically(tmp_path):
    record=evidence('first');size=len(zlib.compress(record[1].encode('utf-8')))
    runtime,session=setup_runtime(tmp_path,size)
    runtime._queue_raw_acquisition(session,*record)
    runtime._event(session,0.,'acquired',{'observation_sha256':record[0]})
    external=evidence('external')
    with connect(runtime.journal_path) as conn:
        conn.execute('INSERT INTO satellite_acquisitions VALUES(?,?)',
            (external[0],zlib.compress(external[1].encode('utf-8'))))
    with pytest.raises(ProtocolError) as error:runtime._flush()
    assert error.value.code=='storage_full' and count_rows(runtime)==(1,0)
    assert runtime._outbox and record[0] in runtime._raw_outbox
