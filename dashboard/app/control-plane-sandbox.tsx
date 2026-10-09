'use client';

import { authenticatedFetch, getApiRoot } from '@/lib/api-session';

import { useEffect, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  CircuitBoard,
  Download,
  Loader2,
  Network,
  Play,
  RotateCcw,
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
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';

const API_ROOT = getApiRoot();

type SandboxMode = 'simulation' | 'shadow' | 'assurance';

interface ControlEvent {
  sequence: number;
  request_id: string;
  record_sha256: string;
  mode: string;
  action: string;
  status: string;
  completed_at_utc: string;
}

interface ControlPlaneStatus {
  available_modes: SandboxMode[];
  physical_mode_available: boolean;
  physical_executor_available: boolean;
  fail_closed: boolean;
  execution_scope: string;
  persistent_topology_available: boolean;
  scenario_runner_available: boolean;
  recent_events: ControlEvent[];
}

interface TopologyNode {
  node_id: string;
  role: 'endpoint' | 'repeater';
  state_version: number;
  memory_state: 'IDLE' | 'STORING';
  available_pair_fidelities: number[];
  memory_ages_s: number[];
}

interface TopologyState {
  topology_id: string;
  topology_version: number;
  nodes: TopologyNode[];
  links: {
    link_id: string;
    left_node_id: string;
    right_node_id: string;
    distance_km: number;
    nominal_pair_fidelity: number;
    active: boolean;
  }[];
}

interface TopologyResponse {
  topology: TopologyState;
  persisted: boolean;
  physical_topology: boolean;
  available_presets: string[];
  recent_events: {
    sequence: number;
    event_kind: string;
    version_after: number;
    event_sha256: string;
  }[];
}

interface SafetyViolation {
  reason_code: string;
  detail: string;
}

interface ControlCycleResponse {
  record_sha256: string;
  idempotent_replay: boolean;
  record: {
    cycle_id: string;
    mode: SandboxMode;
    intent: { action: string; policy_name: string };
    safety: {
      authorized: boolean;
      effective_action: string;
      violations: SafetyViolation[];
    };
    execution: {
      status: string;
      performed_in_simulator: boolean;
      physical_action_performed: boolean;
      details: Record<string, unknown>;
    };
  };
}

interface ScenarioResponse {
  record_sha256: string;
  idempotent_replay: boolean;
  record: {
    scenario_id: string;
    preset: string;
    topology_id: string;
    physical_action_performed: boolean;
    final_topology: TopologyState;
    timeline: {
      step_id: string;
      topology_version_before: number;
      topology_version_after: number;
      cycle: ControlCycleResponse;
      node_after: TopologyNode;
    }[];
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  const response = await authenticatedFetch(`${API_ROOT}${path}`, {
    cache: 'no-store',
    ...init,
    headers,
  });
  const body = (await response.json()) as {
    detail?: string;
    error?: string;
  } & T;
  if (!response.ok) {
    throw new Error(
      body.detail ?? body.error ?? `Falha HTTP ${response.status}`,
    );
  }
  return body;
}

function numericValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function ControlPlaneSandbox() {
  const [mode, setMode] = useState<SandboxMode>('simulation');
  const [fidelity, setFidelity] = useState('0.90');
  const [uncertainty, setUncertainty] = useState('0.01');
  const [pairA, setPairA] = useState('0.90');
  const [pairB, setPairB] = useState('0.90');
  const [telemetryAge, setTelemetryAge] = useState('0.10');
  const [integrity, setIntegrity] = useState(true);
  const [interlock, setInterlock] = useState(true);
  const [ood, setOod] = useState(false);
  const [status, setStatus] = useState<ControlPlaneStatus | null>(null);
  const [events, setEvents] = useState<ControlEvent[]>([]);
  const [topology, setTopology] = useState<TopologyResponse | null>(null);
  const [cycle, setCycle] = useState<ControlCycleResponse | null>(null);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [error, setError] = useState('');
  const [scenarioError, setScenarioError] = useState('');
  const [busy, setBusy] = useState(false);
  const [scenarioBusy, setScenarioBusy] = useState(false);

  async function refreshStatus() {
    const [statusPayload, topologyPayload] = await Promise.all([
      requestJson<ControlPlaneStatus>('/control-plane/status'),
      requestJson<TopologyResponse>('/control-plane/topology'),
    ]);
    setStatus(statusPayload);
    setEvents(statusPayload.recent_events.slice(0, 5));
    setTopology(topologyPayload);
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      requestJson<ControlPlaneStatus>('/control-plane/status'),
      requestJson<TopologyResponse>('/control-plane/topology'),
    ])
      .then(([statusPayload, topologyPayload]) => {
        if (!active) return;
        setStatus(statusPayload);
        setEvents(statusPayload.recent_events.slice(0, 5));
        setTopology(topologyPayload);
      })
      .catch(() => {
        if (active) setStatus(null);
      });
    return () => {
      active = false;
    };
  }, []);

  async function runCycle() {
    setBusy(true);
    setError('');
    setCycle(null);
    try {
      const now = new Date().toISOString();
      const result = await requestJson<ControlCycleResponse>(
        '/control-plane/cycles',
        {
          method: 'POST',
          body: JSON.stringify({
            mode,
            snapshot: {
              request_id: crypto.randomUUID(),
              node_id: 'repeater-sandbox-a',
              timestamp_utc: now,
              state_version: 1,
              predicted_fidelity: numericValue(fidelity, 0.9),
              prediction_uncertainty: numericValue(uncertainty, 0.01),
              telemetry_age_s: numericValue(telemetryAge, 0.1),
              memory_state: 'STORING',
              available_pair_fidelities: [
                numericValue(pairA, 0.9),
                numericValue(pairB, 0.9),
              ],
              memory_ages_s: [0, 0],
              telemetry: {
                source: 'dashboard_sandbox',
                note: 'manual simulated state; not measured WDM evidence',
              },
              out_of_distribution: ood,
              integrity_verified: integrity,
              operator_interlock: interlock,
              provenance: {
                evidence_kind: 'simulated',
                source: 'local-control-plane-sandbox',
                timestamp: now,
                metadata: {
                  evidence_lane: 'bell_fidelity_control',
                  target_semantics: 'simulated_future_bell_fidelity',
                  physical_calibration: false,
                  generated_by: 'dashboard',
                },
              },
              metadata: { wait_time_s: 0.000001 },
            },
          }),
        },
      );
      setCycle(result);
      await refreshStatus();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível executar o ciclo do sandbox.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function runScenario() {
    setScenarioBusy(true);
    setScenarioError('');
    setScenario(null);
    try {
      const result = await requestJson<ScenarioResponse>(
        '/control-plane/scenarios',
        {
          method: 'POST',
          body: JSON.stringify({
            scenario_id: crypto.randomUUID(),
            topology_id: 'sandbox-line-3',
            preset: 'resilience_demo',
            reset_topology: true,
          }),
        },
      );
      setScenario(result);
      await refreshStatus();
    } catch (reason) {
      setScenarioError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível executar o cenário persistente.',
      );
    } finally {
      setScenarioBusy(false);
    }
  }

  function exportCycle() {
    if (!cycle) return;
    const blob = new Blob([JSON.stringify(cycle, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `quantum-control-cycle-${cycle.record.cycle_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function exportScenario() {
    if (!scenario) return;
    const blob = new Blob([JSON.stringify(scenario, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `quantum-network-scenario-${scenario.record.scenario_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const resultFidelity =
    typeof cycle?.record.execution.details.F_t === 'number'
      ? cycle.record.execution.details.F_t
      : typeof cycle?.record.execution.details.F_after === 'number'
        ? cycle.record.execution.details.F_after
        : null;

  return (
    <Card className="sandbox-card" id="sandbox">
      <CardHeader className="sandbox-header">
        <div>
          <p className="section-kicker">Control plane sandbox</p>
          <CardTitle>Orquestração quântica sem atuação física</CardTitle>
          <CardDescription>
            Caminho diagnóstico com entrada manual: execute HALT/WAIT/PURIFY/SWAP,
            teste os interlocks e preserve cada ciclo em um journal SQLite.
          </CardDescription>
        </div>
        <CardAction className="sandbox-badges">
          <Badge variant="outline" className="verified-badge">
            <CircuitBoard aria-hidden="true" />
            {status ? 'Runtime disponível' : 'API necessária'}
          </Badge>
          <Badge variant="outline" className="physical-off-badge">
            <ShieldAlert aria-hidden="true" />
            Físico indisponível
          </Badge>
          <Badge variant="outline">Entrada manual</Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="sandbox-layout">
        <section className="sandbox-controls" aria-label="Estado simulado">
          <div className="sandbox-control-grid">
            <Field>
              <FieldLabel htmlFor="sandbox-mode">Modo</FieldLabel>
              <Select
                value={mode}
                onValueChange={(value) =>
                  value && setMode(value as SandboxMode)
                }
              >
                <SelectTrigger id="sandbox-mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="simulation">Simulation</SelectItem>
                  <SelectItem value="shadow">Shadow</SelectItem>
                  <SelectItem value="assurance">Assurance</SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                Apenas simulation executa o gêmeo virtual.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel htmlFor="predicted-fidelity">
                Fidelidade prevista
              </FieldLabel>
              <Input
                id="predicted-fidelity"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={fidelity}
                onChange={(event) => setFidelity(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="prediction-uncertainty">
                Incerteza
              </FieldLabel>
              <Input
                id="prediction-uncertainty"
                type="number"
                min="0"
                step="0.01"
                value={uncertainty}
                onChange={(event) => setUncertainty(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="telemetry-age">
                Idade da telemetria (s)
              </FieldLabel>
              <Input
                id="telemetry-age"
                type="number"
                min="0"
                step="0.1"
                value={telemetryAge}
                onChange={(event) => setTelemetryAge(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="pair-a">Fidelidade do par A</FieldLabel>
              <Input
                id="pair-a"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={pairA}
                onChange={(event) => setPairA(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="pair-b">Fidelidade do par B</FieldLabel>
              <Input
                id="pair-b"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={pairB}
                onChange={(event) => setPairB(event.target.value)}
              />
            </Field>
          </div>

          <div className="sandbox-interlocks">
            <Field orientation="horizontal">
              <FieldLabel htmlFor="integrity-switch">Integridade</FieldLabel>
              <Switch
                id="integrity-switch"
                checked={integrity}
                onCheckedChange={setIntegrity}
              />
            </Field>
            <Field orientation="horizontal">
              <FieldLabel htmlFor="interlock-switch">Interlock</FieldLabel>
              <Switch
                id="interlock-switch"
                checked={interlock}
                onCheckedChange={setInterlock}
              />
            </Field>
            <Field orientation="horizontal">
              <FieldLabel htmlFor="ood-switch">Forçar OOD</FieldLabel>
              <Switch id="ood-switch" checked={ood} onCheckedChange={setOod} />
            </Field>
          </div>

          <Button onClick={runCycle} disabled={busy || !status} size="lg">
            {busy ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Play aria-hidden="true" />
            )}
            Executar ciclo controlado
          </Button>

          {error && (
            <Alert variant="destructive">
              <ShieldAlert aria-hidden="true" />
              <AlertTitle>Ciclo rejeitado</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </section>

        <section className="sandbox-result" aria-label="Resultado do ciclo">
          {cycle ? (
            <>
              <div className="sandbox-result-hero">
                <span
                  className={
                    cycle.record.safety.authorized
                      ? 'sandbox-result-icon sandbox-result-icon-pass'
                      : 'sandbox-result-icon'
                  }
                >
                  {cycle.record.safety.authorized ? (
                    <CheckCircle2 />
                  ) : (
                    <ShieldAlert />
                  )}
                </span>
                <div>
                  <small>AÇÃO EFETIVA</small>
                  <strong>{cycle.record.safety.effective_action}</strong>
                  <span>
                    intenção {cycle.record.intent.action} ·{' '}
                    {cycle.record.execution.status}
                  </span>
                </div>
              </div>

              <dl className="sandbox-result-metrics">
                <div>
                  <dt>Modo</dt>
                  <dd>{cycle.record.mode}</dd>
                </div>
                <div>
                  <dt>Simulador</dt>
                  <dd>
                    {cycle.record.execution.performed_in_simulator
                      ? 'executado'
                      : 'não executado'}
                  </dd>
                </div>
                <div>
                  <dt>Ação física</dt>
                  <dd>nunca</dd>
                </div>
                <div>
                  <dt>Fidelidade resultante</dt>
                  <dd>
                    {resultFidelity === null
                      ? 'n/a'
                      : resultFidelity.toFixed(6)}
                  </dd>
                </div>
              </dl>

              {cycle.record.safety.violations.length > 0 && (
                <ul className="violation-list">
                  {cycle.record.safety.violations.map((violation) => (
                    <li key={violation.reason_code}>
                      <strong>{violation.reason_code}</strong>
                      <span>{violation.detail}</span>
                    </li>
                  ))}
                </ul>
              )}

              <div className="sandbox-hash">
                <ShieldCheck aria-hidden="true" />
                <span>Registro SHA-256</span>
                <code>{cycle.record_sha256.slice(0, 18)}…</code>
              </div>
              <Button variant="outline" onClick={exportCycle}>
                <Download aria-hidden="true" />
                Exportar ciclo JSON
              </Button>
            </>
          ) : (
            <div className="sandbox-empty">
              <Activity aria-hidden="true" />
              <strong>Aguardando um ciclo</strong>
              <p>
                O cenário inicial possui dois pares simulados com fidelidade
                0,90. Desative um interlock ou force OOD para verificar o HALT
                fail-closed.
              </p>
            </div>
          )}
        </section>

        <section
          className="sandbox-topology"
          aria-label="Topologia quântica simulada persistente"
        >
          <div className="sandbox-topology-header">
            <div>
              <p className="section-kicker">Persistent topology</p>
              <h3>Linha quântica simulada</h3>
              <p>
                Estado versionado em SQLite entre cenários. Os pares são
                injetados pelo simulador e nunca representam hardware real.
              </p>
            </div>
            <div className="sandbox-topology-actions">
              <Badge variant="outline">
                v{topology?.topology.topology_version ?? 0}
              </Badge>
              <Badge variant="outline">
                {topology?.persisted ? 'persistida' : 'blueprint'}
              </Badge>
              <Button
                onClick={runScenario}
                disabled={scenarioBusy || !status?.scenario_runner_available}
              >
                {scenarioBusy ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <RotateCcw aria-hidden="true" />
                )}
                Executar cenário de resiliência
              </Button>
            </div>
          </div>

          <figure className="topology-line">
            <figcaption className="sr-only">
              Topologia linear simulada entre Alice, repetidor e Bob
            </figcaption>
            {topology?.topology.nodes.map((node, index) => (
              <div className="topology-node-wrap" key={node.node_id}>
                {index > 0 && (
                  <span className="topology-link" aria-hidden="true" />
                )}
                <article
                  className={
                    node.role === 'repeater'
                      ? 'topology-node active'
                      : 'topology-node'
                  }
                >
                  <Network aria-hidden="true" />
                  <strong>{node.node_id}</strong>
                  <span>{node.role}</span>
                  <code>
                    {node.memory_state} ·{' '}
                    {node.available_pair_fidelities.length} par(es)
                  </code>
                </article>
              </div>
            ))}
          </figure>

          {scenarioError && (
            <Alert variant="destructive">
              <ShieldAlert aria-hidden="true" />
              <AlertTitle>Cenário rejeitado</AlertTitle>
              <AlertDescription>{scenarioError}</AlertDescription>
            </Alert>
          )}

          {scenario && (
            <div className="scenario-result">
              <div className="scenario-timeline">
                {scenario.record.timeline.map((step) => {
                  const safety = step.cycle.record.safety;
                  return (
                    <article key={step.step_id}>
                      <span
                        className={
                          safety.authorized
                            ? 'status-dot status-dot-positive'
                            : 'status-dot status-dot-negative'
                        }
                      />
                      <div>
                        <strong>{step.step_id}</strong>
                        <span>
                          {step.cycle.record.intent.action} →{' '}
                          {safety.effective_action}
                        </span>
                      </div>
                      <code>
                        v{step.topology_version_before}→v
                        {step.topology_version_after}
                      </code>
                    </article>
                  );
                })}
              </div>
              <div className="scenario-proof">
                <ShieldCheck aria-hidden="true" />
                <span>
                  3 transições auditadas · físico{' '}
                  {scenario.record.physical_action_performed ? 'sim' : 'nunca'}
                </span>
                <code>{scenario.record_sha256.slice(0, 18)}…</code>
                <Button variant="outline" size="sm" onClick={exportScenario}>
                  <Download aria-hidden="true" />
                  Exportar
                </Button>
              </div>
            </div>
          )}
        </section>

        <section className="sandbox-journal" aria-label="Journal recente">
          <div className="sandbox-journal-title">
            <span>Journal recente</span>
            <code>{events.length} evento(s)</code>
          </div>
          {events.length ? (
            <ol>
              {events.map((event) => (
                <li key={event.sequence}>
                  <span className="status-dot status-dot-positive" />
                  <strong>{event.action}</strong>
                  <span>{event.mode}</span>
                  <code>{event.record_sha256.slice(0, 10)}…</code>
                </li>
              ))}
            </ol>
          ) : (
            <p>Nenhum ciclo registrado nesta instalação local.</p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
