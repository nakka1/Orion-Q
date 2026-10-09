"""Present received protocol records, without converting them into sensor waves.

Session metrics and counters are session-wide. Segment observations and current
incident link states are labelled separately. A received zero-result block is
evidence of acquisition, not of a usable quantum route or certified key.
"""
from __future__ import annotations

from .inspector import number, percent, rate


def node_protocol_observations(node_id, live, links):
    """Return table rows and managed link IDs from the current backend snapshot.

    No histories, rates, defaults for absent measurements, or final protocol
    results are generated here. All quantities below are formatted publications.
    """
    if live is None:
        return (), frozenset()
    sessions = live.technologies.get('sessions', {})
    engines = {item.id: item for item in live.links}
    incident = {item.id for item in links}
    rows, managed = [], set()
    for ident, session in sessions.items():
        config = session.get('config', {})
        if node_id not in config.get('path', config.get('endpoints', ())):
            continue
        local_ids = incident.intersection(config.get('link_ids', ()))
        local_ids.update(key for key in incident if key in engines
                         and (engines[key].provenance.get('technology') or {}).get('session_id') == ident)
        managed.update(local_ids)
        metrics, counters = session.get('metrics', {}), session.get('counters', {})
        received_at = number(metrics.get('last_observed_at_s'), '.6f', 's')
        received = received_at != '—'
        state = str(session.get('status', 'not supplied')).replace('_', ' ')
        reason = session.get('reason')
        prefix = f'{ident} · '
        rows.append((prefix+'Estado', state + (f' · {reason}' if reason else ''),
                     'amber' if state != 'operational' else 'ink'))
        rows.append((prefix+'Tecnologias', ' + '.join(config.get('technologies', ()))
                     + ' · sessão ' + ' ↔ '.join(config.get('endpoints', ())), 'ink'))
        rows.append((prefix+'Recepção clássica',
                     (f"{number(counters.get('observations'), '.0f')} observações na sessão · última {received_at}"
                      if received else 'Aguardando aquisição validada e recebimento clássico'),
                     'ink' if received else 'amber'))
        origin = metrics.get('observation_origin') or session.get('source', {}).get('origin')
        source_id = metrics.get('observation_source_id')
        rows.append((prefix+'Origem', (str(origin) if origin else 'não publicada')
                     + (f' · {source_id}' if source_id else ''), 'dim'))
        if local_ids:
            states = []
            for key in sorted(local_ids):
                engine = engines.get(key)
                if engine is None:
                    states.append(f'{key}: estado não publicado')
                    continue
                causes = '; '.join(engine.status_reasons)
                states.append(f"{key}: {engine.status.replace('_', ' ')}"
                              + (f' · {causes}' if causes else ''))
            rows.append((prefix+'Enlaces locais', '\n'.join(states), 'ink'))
        if not received:
            # Runtime may publish initial zero rates before any accepted block.
            # Those counters alone must not be displayed as acquired science.
            continue
        technologies = config.get('technologies', ())
        if 'tf_qkd' in technologies:
            rows.append((prefix+'TF-QKD', f"QBER {percent(metrics.get('qber'))}"
                         + f" · bits sifted {number(metrics.get('sifted_bits'), '.0f')}"
                         + f" · fase {number(metrics.get('phase_reference_residual_rad'), '.4f', 'rad')}", 'ink'))
        if set(technologies).intersection(('satellite_doppler', 'satellite_isl')):
            rows.append((prefix+'Satélite/OGS', f"coincidências {number(metrics.get('coincidences'), '.0f')}"
                         + f" · QBER conjunto {percent(metrics.get('qber'))}"
                         + f" · fidelidade conjunta {number(metrics.get('fidelity'), '.4f')}", 'ink'))
        if set(technologies).intersection(('tf_qkd', 'satellite_doppler', 'satellite_isl')):
            rows.append((prefix+'Chaves · sessão',
                         f"estimativa condicional {rate(metrics.get('conditional_key_estimate_bps'))}"
                         + f" · bits certificados {number(metrics.get('certified_secret_bits'), '.0f')}"
                         + (f" · {metrics['security_status']}" if metrics.get('security_status') else ''), 'ink'))
        if 'entanglement_swapping' in technologies:
            rows.append((prefix+'Pares e operações',
                         f"tentativas {number(counters.get('generation_attempts'), '.0f')}"
                         + f" · pares gerados {number(counters.get('generated_pairs'), '.0f')}"
                         + f" · entregues {number(counters.get('delivered_pairs'), '.0f')}"
                         + f" · geração {number(metrics.get('generation_pair_rate_hz'), '.3g', 'pair/s')}"
                         + f" · entrega {number(metrics.get('delivered_pair_rate_hz'), '.3g', 'pair/s')}"
                         + f" · BSM {percent(metrics.get('bsm_success_fraction'))}"
                         + f" · rendimento de purificação {percent(metrics.get('purification_yield'))}", 'ink'))
        for endpoints, segment in metrics.get('segments', {}).items():
            if node_id not in endpoints.split('|'):
                continue
            heralds = segment.get('heralds')
            fidelity = segment.get('fidelity_estimate') if isinstance(heralds, (int, float)) and not isinstance(heralds, bool) and heralds > 0 else None
            rows.append((prefix+'Segmento '+endpoints.replace('|', ' ↔ '),
                         f"heralds {number(heralds, '.0f')} · recebido {number(segment.get('at_s'), '.6f', 's')}"
                         + f" · fidelidade estimada {number(fidelity, '.4f')}", 'ink'))
    # A partial publication can include an instrument link before its session
    # record. It still must not be interpreted as a legacy polarisation source.
    for key in sorted(incident):
        engine = engines.get(key)
        if engine is not None and engine.provenance.get('technology') and key not in managed:
            managed.add(key)
            message = ('Associação local à sessão não publicada' if
                       engine.provenance['technology'].get('session_id') in sessions else
                       'Registro da sessão não publicado')
            rows.append((key+' · Protocolo', message+' · '
                         + engine.status.replace('_', ' '), 'amber'))
    return tuple(rows), frozenset(managed)
