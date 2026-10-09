"""Reproducible protocol acquisition campaign; no hardware or synthetic UI values."""
import argparse
import json
from pathlib import Path

from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=100)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--scenario',choices=('nominal','degraded','unavailable','recovery'),default='nominal')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.seconds<=86400 or args.seed<0:parser.error('seconds [1,86400], seed >=0 required')
    root=args.output
    root.mkdir(parents=True,exist_ok=True)
    if (root/'protocol.sqlite').exists():parser.error('Use a new output directory to preserve previous evidence.')
    topology,matrix=expand_global(brazil_institutions())
    source=SimulatedAcquisitionSource(args.seed)
    runtime=ProtocolRuntime(root/'protocol.sqlite',topology.topology_id,source=source)
    runtime.sync(topology)
    for ident in runtime.sessions:
        runtime.configure(ident,scenario=args.scenario,seed=args.seed)
    for tick in range(1,args.seconds+1):
        state=runtime.advance(topology,tick)
        if tick%20==0:print(f'{tick}/{args.seconds} simulated seconds',flush=True)
    runtime.export_events(root/'receipts.jsonl')
    for name,content in [('live.json',state),('topology.json',topology.to_dict()),('connection-matrix.json',matrix)]:
        (root/name).write_text(json.dumps(content,indent=2,ensure_ascii=False),encoding='utf-8')
    # This file is explicitly simulated evidence, never a measurement claim.
    (root/'recent-acquisitions-simulated.jsonl').write_text('\n'.join(json.dumps(r) for r in source.records)+'\n',encoding='utf-8')
    rows=['# Campanha operacional modular','',f'Seed: {args.seed}; condição: {args.scenario}; duração: {args.seconds} s simulados. Nenhuma atuação física.','',
          '| Sessão | Estado | Pares gerados | BSM sucesso/tentativas | Purificação sucesso/tentativas | Entregas | QKD condicional (bit/s) | Certificados |',
          '|---|---|---:|---:|---:|---:|---:|---:|']
    for ident,s in state['sessions'].items():
        c,m=s['counters'],s['metrics']
        rows.append(f"| {ident} | {s['status']} | {c.get('generated_pairs',0)} | {c.get('bsm_successes',0)}/{c.get('bsm_attempts',0)} | {c.get('purification_successes',0)}/{c.get('purification_attempts',0)} | {c.get('delivered_pairs',0)} | {m.get('conditional_key_estimate_bps',0):.3g} | {m.get('certified_secret_bits',0)} |")
    (root/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    failures={k:v['reason'] for k,v in state['sessions'].items() if v['status'] in ('error','incompatible')}
    print(json.dumps({'output':str(root),'nodes':len(topology.nodes),'links':len(topology.links),'sessions':len(state['sessions']),
                      'implementation_errors':failures,'physical_actuation':False},ensure_ascii=False))
    return bool(failures)


if __name__=='__main__':raise SystemExit(main())
