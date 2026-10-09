'use client';

import { authenticatedFetch, getApiRoot } from '@/lib/api-session';

import { useEffect, useState } from 'react';
import {
  Activity,
  CircuitBoard,
  Database,
  Loader2,
  RadioTower,
  ShieldAlert,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const API_ROOT = getApiRoot();

interface EvidenceLane {
  lane: string;
  evidence_kind: 'measured' | 'simulated';
  outputs: string[];
  target_semantics: string;
  present_capability: string;
  prediction_status: string;
  decision_scope: string;
  control_plane_ingress_allowed: boolean;
  physical_action_available: boolean;
  metric_conversion_allowed: boolean;
  source_configured: boolean;
}

interface LaneStatus {
  lanes: EvidenceLane[];
  central_hypothesis_status: string;
  cross_metric_conversion_allowed: boolean;
  physical_action_available: boolean;
}

interface OpticalHealth {
  snapshot: {
    quality: { rows: number; devices: number; start_utc: string; end_utc: string };
    prediction_status: string;
    evidence_positive_count: number | null;
    evidence_test_count: number | null;
    provenance: { dataset_hash: string };
  };
}

const labels: Record<string, { title: string; subtitle: string }> = {
  wdm_optical_health: {
    title: 'WDM Optical Health',
    subtitle: 'Telemetria óptica clássica registrada',
  },
  qkd_assurance: {
    title: 'QKD Assurance',
    subtitle: 'QBER e SKR medidos',
  },
  bell_fidelity_control: {
    title: 'Bell Fidelity Control',
    subtitle: 'Controle quântico não físico',
  },
};

const icons = {
  wdm_optical_health: Activity,
  qkd_assurance: RadioTower,
  bell_fidelity_control: CircuitBoard,
};

async function getJson<T>(path: string): Promise<T> {
  const response = await authenticatedFetch(`${API_ROOT}${path}`, { cache: 'no-store' });
  const body = (await response.json()) as T & { detail?: string; error?: string };
  if (!response.ok) {
    throw new Error(body.detail ?? body.error ?? `Falha HTTP ${response.status}`);
  }
  return body;
}

export function EvidenceLanesPanel() {
  const [status, setStatus] = useState<LaneStatus | null>(null);
  const [optical, setOptical] = useState<OpticalHealth | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    getJson<LaneStatus>('/evidence-lanes')
      .then(async (payload) => {
        if (!active) return;
        setStatus(payload);
        const opticalLane = payload.lanes.find(
          (item) => item.lane === 'wdm_optical_health',
        );
        if (opticalLane?.source_configured) {
          const snapshot = await getJson<OpticalHealth>(
            '/evidence-lanes/optical-health',
          );
          if (active) setOptical(snapshot);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : 'API indisponível');
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="lane-section" id="lanes" aria-labelledby="lane-title">
      <div className="lane-heading">
        <div>
          <p className="section-kicker">Contrato de evidência</p>
          <h2 id="lane-title">Três métricas, três permissões distintas</h2>
          <p>
            O dashboard reúne as evidências sem converter BER, QBER ou SKR em
            fidelidade Bell. Somente a trilha simulada chega ao sandbox.
          </p>
        </div>
        <Badge variant="outline">
          {error ? <ShieldAlert aria-hidden="true" /> : <Database aria-hidden="true" />}
          {error ? 'API indisponível' : 'Conversão cruzada bloqueada'}
        </Badge>
      </div>

      {!status && !error ? (
        <div className="lane-loading">
          <Loader2 className="animate-spin" aria-hidden="true" />
          Validando trilhas…
        </div>
      ) : null}

      {error ? <p className="lane-error">{error}</p> : null}

      <div className="lane-grid">
        {status?.lanes.map((lane) => {
          const Icon = icons[lane.lane as keyof typeof icons] ?? Database;
          const copy = labels[lane.lane] ?? {
            title: lane.lane,
            subtitle: lane.target_semantics,
          };
          const realOptical = lane.lane === 'wdm_optical_health' ? optical : null;
          return (
            <Card className={`lane-card lane-card-${lane.evidence_kind}`} key={lane.lane}>
              <CardHeader>
                <span className="lane-icon">
                  <Icon aria-hidden="true" />
                </span>
                <div>
                  <CardTitle>{copy.title}</CardTitle>
                  <p>{copy.subtitle}</p>
                </div>
                <Badge variant={lane.evidence_kind === 'measured' ? 'secondary' : 'outline'}>
                  {lane.evidence_kind === 'measured' ? 'Medido' : 'Simulado'}
                </Badge>
              </CardHeader>
              <CardContent>
                <dl className="lane-facts">
                  <div>
                    <dt>Capacidade atual</dt>
                    <dd>{lane.present_capability}</dd>
                  </div>
                  <div>
                    <dt>Estado preditivo</dt>
                    <dd>{lane.prediction_status}</dd>
                  </div>
                  <div>
                    <dt>Controle</dt>
                    <dd className={lane.control_plane_ingress_allowed ? 'lane-sim' : 'lane-blocked'}>
                      {lane.control_plane_ingress_allowed
                        ? 'Elegível ao sandbox + supervisor'
                        : 'PURIFY/SWAP bloqueados'}
                    </dd>
                  </div>
                </dl>
                {lane.lane === 'wdm_optical_health' ? (
                  <div className="lane-source">
                    {realOptical ? (
                      <>
                        <strong>
                          {realOptical.snapshot.quality.rows.toLocaleString('pt-BR')} linhas ·{' '}
                          {realOptical.snapshot.quality.devices} dispositivos
                        </strong>
                        <span>
                          Resultado registrado: {realOptical.snapshot.evidence_positive_count}/
                          {realOptical.snapshot.evidence_test_count} testes positivos
                        </span>
                        <code>{realOptical.snapshot.provenance.dataset_hash.slice(0, 16)}…</code>
                      </>
                    ) : (
                      <span>
                        Fonte real não configurada nesta execução da API; nenhum caminho é
                        adivinhado pelo navegador.
                      </span>
                    )}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {status ? (
        <div className="lane-boundary">
          <ShieldAlert aria-hidden="true" />
          <span>{status.central_hypothesis_status}</span>
        </div>
      ) : null}
    </section>
  );
}
