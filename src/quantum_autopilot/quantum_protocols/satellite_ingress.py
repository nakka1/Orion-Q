"""Authenticated, opt-in, read-only OGS WebSocket ingress and durable replay.

This receives decoded gateway records, not RF/CCSDS frames or photon states.
An external gateway must perform mission-specific decoding and clock calibration.
The transport exposes no actuator or QKD key distribution endpoint.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock, Thread

from .contracts import Acquisition, ProtocolError, canonical, digest
from .satellite_contracts import KINDS, assemble, validate_packet

MAX_MESSAGE_BYTES=8*1024*1024


class OGSStore:
    def __init__(self,path,*,source_id='ogs_gateway',origin='physical',max_pending=128,max_age_s=60.,max_pending_bytes=64*1024*1024,max_persisted_bytes=512*1024*1024):
        if origin not in ('physical','measurement_file','simulated') or not source_id:
            raise ProtocolError('wrong_source','explicit OGS source identity and origin required')
        self.path=Path(path);self.source_id=source_id;self.origin=origin
        self.max_pending=max_pending;self.max_age_s=max_age_s;self.max_pending_bytes=max_pending_bytes
        self.max_persisted_bytes=max_persisted_bytes
        self._requests={}
        self.lock=Lock();self.counters={'accepted':0,'duplicates':0,'rejected':0,'sequence_gaps':0,'expired':0}
        with closing(self._connect()) as conn,conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS ogs_packets(
                session_id TEXT,kind TEXT,start_s REAL,end_s REAL,sequence INTEGER,
                sha256 TEXT,json TEXT,received_at_s REAL,consumed INTEGER DEFAULT 0,
                PRIMARY KEY(session_id,kind,start_s));
                CREATE TABLE IF NOT EXISTS ogs_streams(session_id TEXT,kind TEXT,last_sequence INTEGER,
                last_sha256 TEXT,PRIMARY KEY(session_id,kind));
                CREATE TABLE IF NOT EXISTS ogs_identity(source_id TEXT,origin TEXT);''')
            identity=conn.execute('SELECT source_id,origin FROM ogs_identity').fetchone()
            if identity and identity!=(source_id,origin):raise ProtocolError('wrong_source','persistent OGS database belongs to another source')
            if not identity:conn.execute('INSERT INTO ogs_identity VALUES(?,?)',(source_id,origin))

    def _connect(self):
        return sqlite3.connect(self.path,timeout=5.)

    def submit(self,packet,now_s):
        try:
            return self._submit(packet,now_s)
        except (ProtocolError,ValueError,KeyError,TypeError):
            self.counters['rejected']+=1
            raise

    def _submit(self,packet,now_s):
        validate_packet(packet);body=canonical(packet)
        body_bytes=len(body.encode('utf-8'))
        if body_bytes>MAX_MESSAGE_BYTES:
            raise ProtocolError('message_limit','OGS packet exceeds 8 MiB')
        if packet['source_id']!=self.source_id or packet['origin']!=self.origin:
            raise ProtocolError('wrong_source','gateway source identity/origin mismatch')
        if packet['end_s']>now_s+1e-9 or now_s-packet['end_s']>self.max_age_s:
            raise ProtocolError('stale_observation','OGS block is in the future or outside the freshness policy')
        sha=digest(packet);session,kind=packet['session_id'],packet['kind']
        with self.lock,closing(self._connect()) as conn,conn:
            expired=conn.execute('UPDATE ogs_packets SET consumed=1 WHERE consumed=0 AND end_s<?',(now_s-self.max_age_s,)).rowcount
            self.counters['expired']+=expired
            previous=conn.execute('SELECT last_sequence,last_sha256 FROM ogs_streams WHERE session_id=? AND kind=?',(session,kind)).fetchone()
            if previous and packet['sequence']<=previous[0]:
                if packet['sequence']==previous[0] and sha==previous[1]:
                    self.counters['duplicates']+=1
                    return {'status':'duplicate','sha256':sha,'sequence':packet['sequence']}
                raise ProtocolError('replay','stale/conflicting OGS stream sequence')
            size,count=conn.execute('SELECT coalesce(sum(length(cast(json as blob))),0),count(*) FROM ogs_packets WHERE consumed=0').fetchone()
            if count>=self.max_pending or size+body_bytes>self.max_pending_bytes:
                raise ProtocolError('backpressure','OGS intake full; retry after backend consumes observations')
            persisted=conn.execute('SELECT coalesce(sum(length(cast(json as blob))),0) FROM ogs_packets').fetchone()[0]
            if persisted+body_bytes>self.max_persisted_bytes:
                raise ProtocolError('storage_full','OGS evidence quota reached; export/archive and select a new journal; existing observations are preserved')
            existing=conn.execute('SELECT consumed FROM ogs_packets WHERE session_id=? AND kind=? AND start_s=?',(session,kind,packet['start_s'])).fetchone()
            if existing:raise ProtocolError('replay','OGS block already submitted/consumed')
            companions=[json.loads(r[0]) for r in conn.execute('SELECT json FROM ogs_packets WHERE session_id=? AND start_s=? AND consumed=0',(session,packet['start_s']))]
            if len(companions)==2:assemble([*companions,packet])
            if previous and packet['sequence']>previous[0]+1:self.counters['sequence_gaps']+=packet['sequence']-previous[0]-1
            conn.execute('INSERT INTO ogs_packets VALUES(?,?,?,?,?,?,?,?,0)',(session,kind,packet['start_s'],packet['end_s'],packet['sequence'],sha,body,now_s))
            conn.execute('INSERT INTO ogs_streams VALUES(?,?,?,?) ON CONFLICT(session_id,kind) DO UPDATE SET last_sequence=excluded.last_sequence,last_sha256=excluded.last_sha256',(session,kind,packet['sequence'],sha))
            self.counters['accepted']+=1
        return {'status':'accepted','sha256':sha,'sequence':packet['sequence']}

    def take(self,command,config,now_s):
        with self.lock,closing(self._connect()) as conn,conn:
            rows=conn.execute('SELECT json FROM ogs_packets WHERE session_id=? AND abs(start_s-?)<1e-9 AND consumed=0',(config.session_id,command.emitted_at_s)).fetchall()
            if len(rows)!=3:return None
            messages=[json.loads(r[0]) for r in rows]
            raw=assemble(messages,parameters=config.parameters)
            if abs(raw['end_s']-command.emitted_at_s-command.arguments['duration_s'])>1e-9:
                raise ProtocolError('wrong_binding','OGS duration differs from requested block')
            if any(m['quantum_channel_ids']!=list(command.quantum_channel_ids) or m['classical_channel_ids']!=list(command.classical_channel_ids) for m in messages):
                raise ProtocolError('wrong_channel','OGS channels differ from the session')
            if now_s-raw['end_s']>self.max_age_s:raise ProtocolError('stale_observation','OGS block expired before processing')
            conn.execute('UPDATE ogs_packets SET consumed=1 WHERE session_id=? AND start_s=?',(config.session_id,raw['start_s']))
            return raw,max(m['sequence'] for m in messages)

    def advertise(self,command,now_s,*,completed=False):
        """Publish observation windows, never actuator instructions or secrets."""
        with self.lock:
            self._requests={k:v for k,v in self._requests.items() if v['expires_s']>=now_s}
            if completed:
                self._requests.pop(command.command_id,None)
                return
            if command.command_id not in self._requests and len(self._requests)>=128:
                raise ProtocolError('backpressure','OGS observation window catalogue is full')
            self._requests[command.command_id]={
                'session_id':command.session_id,'start_s':command.emitted_at_s,
                'end_s':command.emitted_at_s+command.arguments['duration_s'],
                'expires_s':command.deadline_s,'quantum_channel_ids':list(command.quantum_channel_ids),
                'classical_channel_ids':list(command.classical_channel_ids)}

    def gateway_status(self,now_s):
        with self.lock:
            windows=[v.copy() for v in self._requests.values() if v['expires_s']>=now_s]
        return {'schema':'orion.ogs-status.v2','clock_s':now_s,
                'source_id':self.source_id,'origin':self.origin,'observation_windows':windows,
                'scope':'read-only acquisition bindings; gateway buffers/normalizes measured records; no hardware commands',
                'physical_actuation':False}

    def status(self):
        with self.lock,closing(self._connect()) as conn:
            pending=conn.execute('SELECT count(*) FROM ogs_packets WHERE consumed=0').fetchone()[0]
        return {**self.counters,'pending_packets':pending,'source_id':self.source_id,'origin':self.origin,
                'persisted_limit_bytes':self.max_persisted_bytes,
                'physical_adapter_validated':False,'physical_actuation':False,'transport':'OGS canonical v2 read-only'}


class SatelliteStreamSource:
    physical_validated=False

    def __init__(self,store,clock):
        self.store=store;self.source_id=store.source_id;self.origin=store.origin;self.clock=clock

    def acquire(self,command,config,environment):
        if command.operation!='satellite_block':return None
        now=self.clock();self.store.advertise(command,now)
        record=self.store.take(command,config,now)
        if record is None:return None
        self.store.advertise(command,now,completed=True)
        raw,sequence=record
        return Acquisition(command.command_id,command.session_id,command.operation,self.source_id,self.origin,
            sequence,raw['end_s'],0.,command.quantum_channel_ids,command.classical_channel_ids,raw)


class OGSWebSocketServer:
    def __init__(self,store,clock,*,port=0,credential_ref,host='127.0.0.1',security=None):
        if host not in ('127.0.0.1','localhost','::1'):
            raise ProtocolError('unsafe_bind','local OGS gateway only; use an authenticated TLS reverse proxy for remote stations')
        if not isinstance(credential_ref,str):
            raise ProtocolError('missing_credential','secure credential reference required')
        self.security=security
        self._expected_subject=None
        if security is None:
            if store.origin != 'simulated':
                raise ProtocolError('authentication_required','physical OGS ingress requires common backend identity')
            if not credential_ref.startswith('env:'):
                raise ProtocolError('missing_credential','isolated simulated fixture requires an env: reference')
            token=os.environ.get(credential_ref[4:],'')
            if len(token)<32:raise ProtocolError('missing_credential','simulated fixture credential unavailable')
        else:
            if not credential_ref.startswith('vault:') or not credential_ref[6:]:
                raise ProtocolError('missing_credential','OGS ingress requires a vault: session reference')
            try:
                token=security.vault.get_bytes(credential_ref[6:]).decode('ascii')
                self._expected_subject=self._authenticate(token).subject
            except (PermissionError,ValueError,UnicodeError,RuntimeError):
                raise ProtocolError('authentication_required','OGS session reference is unavailable or revoked') from None
        try:
            from websockets.sync.server import serve
        except ImportError as exc:
            raise ProtocolError('adapter_unavailable','install ORION-Q[satellite] for WebSocket ingress') from exc
        self.store=store;self.clock=clock;self._token=token;self._clients=set();self._lock=Lock()
        def handshake(connection,request):
            path=request.path
            if path not in {f'/ogs/v2/{k}' for k in (*KINDS,'status')}:
                return connection.respond(404,'unknown OGS stream')
            if len(request.headers.get_all('Authorization')) != 1:
                return connection.respond(401,'authenticated OGS gateway required')
            authorization=request.headers.get('Authorization','')
            if security is None:
                if not hmac.compare_digest(authorization,'Bearer '+token):
                    return connection.respond(401,'authenticated OGS gateway required')
            else:
                try:
                    principal=self._request_identity(authorization)
                    with security.identity_scope(principal):
                        self._authorize(path)
                except PermissionError:
                    return connection.respond(403,'OGS identity or resource permission rejected')
            if request.headers.get('Origin') is not None:
                return connection.respond(403,'browser-origin OGS connections are not accepted')
        self.server=serve(self._handle,host,port,process_request=handshake,origins=[None],
                          max_size=MAX_MESSAGE_BYTES,max_queue=4,compression=None,open_timeout=3.,
                          ping_interval=10.,ping_timeout=10.,close_timeout=1.,server_header=None)
        self.port=self.server.socket.getsockname()[1]
        self.thread=Thread(target=self.server.serve_forever,name='orion-ogs-read-only',daemon=True)
        self.thread.start()

    def _authenticate(self,token):
        return self.security.authenticate_oidc(token) if token.count('.')==2 else self.security.session(token)

    def _request_identity(self,authorization):
        from ..security import AuthenticationError
        if not isinstance(authorization,str) or not authorization.startswith('Bearer ') or len(authorization)>16400:
            raise AuthenticationError('OGS authenticated identity required')
        principal=self._authenticate(authorization[7:])
        if principal.subject != self._expected_subject:
            raise AuthenticationError('OGS identity binding rejected')
        return principal

    def _authorize(self,path):
        action='sources.read' if path=='/ogs/v2/status' else 'ingestion.write'
        return self.security.authorize(action,'source:'+self.store.source_id+'/'+path.rsplit('/',1)[-1])

    def _handle(self,connection):
        with self._lock:
            if len(self._clients)>=4:
                connection.close(1013,'OGS connection capacity reached');return
            self._clients.add(connection)
        try:
            while True:
                try:message=connection.recv(timeout=30.)
                except TimeoutError:break
                try:
                    if self.security is not None:
                        principal=self._request_identity(connection.request.headers.get('Authorization',''))
                        with self.security.identity_scope(principal):
                            self._authorize(connection.request.path)
                            data=message.encode('utf-8') if isinstance(message,str) else message
                            self.security.audit.record(principal,'ingestion.receive',
                                'source:'+self.store.source_id+'/'+connection.request.path.rsplit('/',1)[-1],
                                'accepted',{'artifact_sha256':hashlib.sha256(data).hexdigest()})
                            result=self._receive(connection,message)
                    else:
                        result=self._receive(connection,message)
                except PermissionError:
                    connection.send(canonical({'status':'rejected','code':'authentication_required'}))
                    connection.close(1008,'OGS identity unavailable or permission revoked');break
                except (ProtocolError,ValueError,TypeError,KeyError,RecursionError) as exc:
                    result={'status':'rejected','code':getattr(exc,'code','invalid_packet')}
                connection.send(canonical(result))
        except Exception as exc:
            from websockets.exceptions import ConnectionClosed
            if not isinstance(exc,ConnectionClosed):raise
        finally:
            with self._lock:self._clients.discard(connection)
            connection.close()

    def _receive(self,connection,message):
        from ..security.http_boundary import strict_object
        data=message.encode('utf-8') if isinstance(message,str) else message
        packet=strict_object(data)
        if connection.request.path=='/ogs/v2/status':
            if packet!={'query':'status'}:
                raise ProtocolError('invalid_packet','status query shape rejected')
            return self.store.gateway_status(self.clock())
        if packet.get('kind')!=connection.request.path.rsplit('/',1)[-1]:
            raise ProtocolError('wrong_stream','packet kind differs from WebSocket path')
        return self.store.submit(packet,self.clock())

    def close(self):
        with self._lock:clients=list(self._clients)
        for client in clients:client.close()
        self.server.shutdown();self.thread.join(timeout=3.);self._token=''
