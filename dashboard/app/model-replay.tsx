'use client';

import { authenticatedFetch, getApiRoot } from '@/lib/api-session';

import { useEffect, useState } from 'react';
import {
  Activity,
  BrainCircuit,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

const API_ROOT = getApiRoot();

interface ScientificStatus {
  approved_for_control: false;
  physical_validation: false;
  paired_measured_wdm_quantum_validation: false;
  v5_25_registered_neural_improvements: string;
  v5_26_registered_neural_improvements: string;
  statement: string;
}

interface ModelStatus {
  configured: boolean;
  valid: boolean;
  replay_available: boolean;
  reason?: string;
  detail?: string;
  bundle_sha256?: string;
  bundle_hash_pinned?: boolean;
  architecture?: { name: string; parameters: Record<string, number> };
  model?: {
    name: string;
    version: string;
    output_kind: string;
    model_sha256: string;
    feature_columns: string[];
    window_size: number;
    target_semantics: string;
  };
  policy?: {
    task: string;
    policy_spec_sha256: string;
    max_prediction_uncertainty: number;
    probability_margin: number;
  };
  replay_record_count?: number;
  max_replay_cycles_per_request?: number;
  scientific_status?: ScientificStatus;
  physical_action_available: false;
}

interface ReplayRow {
  index: number;
  timestamp_utc: string;
  predicted_fidelity: number;
  uncertainty: number;
  interval_lower: number;
  interval_upper: number;
  simulated_actual_fidelity: number | null;
  out_of_distribution: boolean;
  intended_action: string;
  effective_action: string;
  outcome_category: string;
  safety_reason_codes: string[];
  record_sha256: string;
  physical_action_performed: false;
}

interface ReplayResponse {
  mode: 'shadow';
  bundle_sha256: string;
  model_sha256: string;
  policy_spec_sha256: string;
  returned_count: number;
  trace_record_count: number;
  action_counts: Record<string, number>;
  abstention_counts: Record<string, number>;
  rows: ReplayRow[];
  scientific_status: ScientificStatus;
  physical_action_performed: false;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  const response = await authenticatedFetch(`${API_ROOT}${path}`, {
    cache: 'no-store',
    ...init,
    headers,
  });
  const body = (await response.json()) as T & { detail?: string; error?: string };
  if (!response.ok) {
    throw new Error(body.detail ?? body.error ?? `Falha HTTP ${response.status}`);
  }
  return body;
}

function ReplayChart({ rows }: { rows: ReplayRow[] }) {
  const width = 760;
  const height = 250;
  const left = 42;
  const right = 18;
  const top = 18;
  const bottom = 34;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (index: number) =>
    left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
  const y = (value: number) => top + (1 - value) * plotHeight;
  const predicted = rows
    .map((row, index) => `${x(index)},${y(row.predicted_fidelity)}`)
    .join(' ');
  const actual = rows
    .map((row, index) =>
      row.simulated_actual_fidelity === null
        ? null
        : `${x(index)},${y(row.simulated_actual_fidelity)}`,
    )
    .filter(Boolean)
    .join(' ');

  return (
    <figure className="model-replay-chart">
      <svg viewBox={`0 0 ${width} ${height}`}>
        <title>Fidelidade prevista, incerteza e alvo simulado retrospectivo</title>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line x1={left} y1={y(tick)} x2={width - right} y2={y(tick)} className="replay-grid-line" />
            <text x={left - 9} y={y(tick) + 4} textAnchor="end" className="replay-axis-label">
              {tick.toFixed(2)}
            </text>
          </g>
        ))}
        {rows.map((row, index) => (
          <line
            key={`uncertainty-${row.index}`}
            x1={x(index)}
            x2={x(index)}
            y1={y(row.interval_upper)}
            y2={y(row.interval_lower)}
            className="replay-uncertainty"
          />
        ))}
        {actual && <polyline points={actual} className="replay-actual-line" />}
        <polyline points={predicted} className="replay-predicted-line" />
        {rows.map((row, index) => (
          <circle
            key={`prediction-${row.index}`}
            cx={x(index)}
            cy={y(row.predicted_fidelity)}
            r={row.out_of_distribution ? 5 : 3}
            className={row.out_of_distribution ? 'replay-point replay-point-ood' : 'replay-point'}
          />
        ))}
        <text x={width / 2} y={height - 7} textAnchor="middle" className="replay-axis-label">
          janela temporal no holdout
        </text>
      </svg>
      <figcaption>
        <span><i className="legend-predicted" /> previsão</span>
        <span><i className="legend-actual" /> alvo simulado retrospectivo</span>
        <span><i className="legend-uncertainty" /> ± incerteza</span>
        <span><i className="legend-ood" /> OOD</span>
      </figcaption>
    </figure>
  );
}

export function ModelReplayPanel() {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    requestJson<ModelStatus>('/control-plane/model')
      .then((payload) => {
        if (active) setStatus(payload);
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : 'API indisponível');
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function runReplay() {
    setBusy(true);
    setError('');
    try {
      const result = await requestJson<ReplayResponse>(
        '/control-plane/model-replay',
        {
          method: 'POST',
          body: JSON.stringify({
            start_index: 0,
            limit: Math.min(status?.replay_record_count ?? 25, 25),
          }),
        },
      );
      setReplay(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Replay rejeitado');
    } finally {
      setBusy(false);
    }
  }

  const scientific = replay?.scientific_status ?? status?.scientific_status;

  return (
    <Card className="model-replay-card" id="model-replay">
      <CardHeader className="sandbox-header">
        <div>
          <p className="section-kicker">Frozen model replay</p>
          <CardTitle>Modelo WDM auditável em shadow</CardTitle>
          <CardDescription>
            Reproduz um holdout limitado com pesos numéricos seguros, política congelada e rastreio por SHA-256.
          </CardDescription>
        </div>
        <CardAction className="sandbox-badges">
          <Badge variant="outline" className={status?.valid ? 'verified-badge' : 'physical-off-badge'}>
            <BrainCircuit aria-hidden="true" />
            {status?.valid ? 'Bundle validado' : 'Bundle não configurado'}
          </Badge>
          <Badge variant="outline" className="physical-off-badge">
            <ShieldAlert aria-hidden="true" /> não aprovado
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="model-replay-content">
        {!status?.configured && (
          <Alert>
            <Activity aria-hidden="true" />
            <AlertTitle>Configure o bundle no servidor local</AlertTitle>
            <AlertDescription>
              Gere o artefato com build_wdm_model_bundle.py e inicie a API com --wdm-model-bundle. O navegador nunca escolhe um arquivo local.
            </AlertDescription>
          </Alert>
        )}

        {status?.configured && !status.valid && (
          <Alert variant="destructive">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>Bundle rejeitado</AlertTitle>
            <AlertDescription>{status.detail ?? status.reason}</AlertDescription>
          </Alert>
        )}

        {status?.valid && status.model && status.policy && (
          <>
            <div className="model-status-grid">
              <div><span>Modelo</span><strong>{status.model.name}</strong><small>{status.model.output_kind}</small></div>
              <div><span>Janela</span><strong>{status.model.window_size}</strong><small>{status.model.feature_columns.length} features WDM</small></div>
              <div><span>Política</span><strong>link purification</strong><small>margem {status.policy.probability_margin.toFixed(3)}</small></div>
              <div><span>Artefato</span><strong>{status.bundle_hash_pinned ? 'hash fixado' : 'hash calculado'}</strong><small>{status.replay_record_count} registros</small></div>
            </div>
            <div className="model-hash-row">
              <ShieldCheck aria-hidden="true" />
              <span>bundle</span><code>{status.bundle_sha256?.slice(0, 18)}…</code>
              <span>policy</span><code>{status.policy.policy_spec_sha256.slice(0, 18)}…</code>
            </div>
            <Button onClick={runReplay} disabled={busy || !status.replay_available}>
              {busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
              Reproduzir 25 janelas
            </Button>
          </>
        )}

        {scientific && (
          <Alert variant="destructive" className="negative-result-alert">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>
              Ganho neural registrado: v5.25 {scientific.v5_25_registered_neural_improvements} · v5.26 {scientific.v5_26_registered_neural_improvements}
            </AlertTitle>
            <AlertDescription>{scientific.statement}</AlertDescription>
          </Alert>
        )}

        {error && (
          <Alert variant="destructive">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>Replay rejeitado</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {replay && (
          <div className="model-replay-results">
            <ReplayChart rows={replay.rows} />
            <div className="replay-action-summary">
              {Object.entries(replay.action_counts).map(([action, count]) => (
                <div key={action}><span>{action}</span><strong>{count}</strong></div>
              ))}
              <div><span>Ação física</span><strong>0</strong></div>
            </div>
            <div className="replay-row-list" aria-label="Decisões por janela">
              {replay.rows.map((row) => (
                <article key={row.index}>
                  <span className={row.out_of_distribution ? 'status-dot status-dot-negative' : 'status-dot status-dot-positive'} />
                  <code>#{row.index}</code>
                  <span>F̂ {row.predicted_fidelity.toFixed(3)} ± {row.uncertainty.toFixed(3)}</span>
                  <strong>{row.intended_action} → {row.effective_action}</strong>
                  <small>{row.outcome_category}</small>
                </article>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
