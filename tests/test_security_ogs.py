"""Satellite ingress uses the same revocable backend identity per message."""
import json

import pytest

pytest.importorskip('websockets')
from websockets.exceptions import InvalidStatus  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from quantum_autopilot.security import SecurityRuntime  # noqa: E402
from quantum_autopilot.quantum_protocols.contracts import ProtocolError  # noqa: E402
from quantum_autopilot.quantum_protocols.satellite_ingress import OGSStore, OGSWebSocketServer  # noqa: E402


@pytest.fixture
def secured(tmp_path):
    runtime=SecurityRuntime(tmp_path/'security')
    identity=runtime.grant_identity('service:ogs',['operator'],['source:ogs_gateway/*'])
    token=runtime.issue_service_session(identity.subject)
    runtime.vault.put_bytes('ogs-session',token.encode())
    store=OGSStore(tmp_path/'ogs.sqlite',origin='physical')
    server=OGSWebSocketServer(store,lambda:10.,security=runtime,credential_ref='vault:ogs-session')
    try:
        yield runtime,identity,token,store,server
    finally:
        server.close()


def test_anonymous_gateway_is_rejected_before_receiving_observations(secured):
    _,_,_,store,server=secured
    with pytest.raises(InvalidStatus):
        connect(f'ws://127.0.0.1:{server.port}/ogs/v2/status')
    assert store.status()['accepted']==0


def test_identity_revocation_is_rechecked_on_open_websocket(secured):
    runtime,identity,token,store,server=secured
    with connect(f'ws://127.0.0.1:{server.port}/ogs/v2/status',
                 additional_headers={'Authorization':'Bearer '+token}) as channel:
        channel.send(json.dumps({'query':'status'}))
        assert json.loads(channel.recv())['schema']=='orion.ogs-status.v2'
        runtime.revoke_identity(identity.subject)
        channel.send(json.dumps({'query':'status'}))
        response=json.loads(channel.recv())
        assert response=={'status':'rejected','code':'authentication_required'}
    assert store.status()['accepted']==0


def test_channel_resource_scope_is_required(secured):
    runtime,identity,_,store,server=secured
    runtime.grant_identity(identity.subject,['operator'],['source:other/*'])
    replacement=runtime.issue_service_session(identity.subject)
    with pytest.raises(InvalidStatus):
        connect(f'ws://127.0.0.1:{server.port}/ogs/v2/tdc',
                additional_headers={'Authorization':'Bearer '+replacement})
    assert store.status()['accepted']==0


def test_physical_origin_rejects_legacy_environment_token(tmp_path,monkeypatch):
    monkeypatch.setenv('OGS_FIXTURE_ONLY_TOKEN','x'*64)
    store=OGSStore(tmp_path/'physical.sqlite',origin='physical')
    with pytest.raises(ProtocolError,match='common backend identity'):
        OGSWebSocketServer(store,lambda:10.,credential_ref='env:OGS_FIXTURE_ONLY_TOKEN')


def test_duplicate_json_is_rejected_without_echoing_body_or_secrets(secured):
    runtime,_,token,store,server=secured
    with connect(f'ws://127.0.0.1:{server.port}/ogs/v2/status',
                 additional_headers={'Authorization':'Bearer '+token}) as channel:
        channel.send('{"query":"status","query":"SECRET-FIXTURE"}')
        response=json.loads(channel.recv())
        assert response=={'status':'rejected','code':'invalid_packet'}
    assert store.status()['accepted']==0
    assert 'SECRET-FIXTURE' not in runtime.audit.path.read_text()
    assert token not in runtime.audit.path.read_text()
    assert runtime.audit.verify()['valid']
