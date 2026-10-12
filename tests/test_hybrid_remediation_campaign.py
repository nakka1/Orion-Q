"""Evidence admission must reject lost ACKs and leaked private SNS records."""
from copy import deepcopy
import importlib.util
from pathlib import Path

from quantum_autopilot.quantum_protocols.contracts import digest

SPEC=importlib.util.spec_from_file_location('remediation_campaign',Path(__file__).parents[1]/'tools/run_hybrid_remediation_campaign.py')
campaign=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


def receipts(events):
    rows=[]
    previous=''
    for index,(event,detail) in enumerate(events):
        body={'session_id':campaign.TF,'command_id':'block1','event':event,
              'at_s':float(index),'detail':detail,'physical_actuation':False,'previous_sha256':previous}
        row={**body,'receipt_sha256':digest(body)}
        previous=row['receipt_sha256']
        rows.append(row)
    return rows


def checks(rows):
    result={key:True for key in ('receipt_hash_chain','physical_actuation_blocked',
        'metrics_after_classical_receipt','finite_sns_all_public_receipts','private_preparations_not_transmitted')}
    campaign.validate_receipts(rows,result)
    return result


def nominal():
    body={'message_id':'block1:public:1','command_id':'block1','session_id':campaign.TF,
          'stage':'reconciliation','sequence':1,'payload':{'parity':1},
          'transmitted_at_s':1.,'received_at_s':2.}
    public={**body,'receipt_sha256':digest(body)}
    return [('classical_received',{}),('postprocessing_classical_transmitted',public),
            ('postprocessing_classical_received',public),('metrics_computed',{})]


def test_lost_public_ack_invalidates_completed_block():
    assert all(checks(receipts(nominal())).values())
    lost=[item for item in nominal() if item[0]!='postprocessing_classical_received']
    assert not checks(receipts(lost))['finite_sns_all_public_receipts']


def test_private_input_leak_rejected_even_with_valid_hash_chain():
    rows=nominal()
    rows[1]=('postprocessing_classical_transmitted',{'payload':{'alice_preparation':[0,1]}})
    result=checks(receipts(rows))
    assert result['receipt_hash_chain'] and not result['private_preparations_not_transmitted']


def test_tampering_and_repeated_consumption_rejected():
    rows=deepcopy(receipts(nominal()))
    rows[0]['detail']={'altered':True}
    assert not checks(rows)['receipt_hash_chain']
    consumed=receipts([('resources_reserved_and_consumed',{'parent_ids':['pair1','pair2']}),
                       ('resources_reserved_and_consumed',{'parent_ids':['pair1','pair3']})])
    for row in consumed:
        row['session_id']=campaign.SESSION_ID
    assert not checks(consumed)['parents_consumed_once']


def test_metric_published_before_control_rejected():
    rows=receipts([('metrics_computed',{}),('classical_received',{})])
    assert not checks(rows)['metrics_after_classical_receipt']


def test_mismatched_ack_rejected_despite_valid_event_hashes():
    rows=nominal()
    receive={**rows[2][1],'stage':'other_stage','sequence':99}
    receive['receipt_sha256']=digest({k:v for k,v in receive.items() if k!='receipt_sha256'})
    rows[2]=('postprocessing_classical_received',receive)
    result=checks(receipts(rows))
    assert result['receipt_hash_chain'] and not result['finite_sns_all_public_receipts']
