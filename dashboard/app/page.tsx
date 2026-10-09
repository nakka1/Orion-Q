import {
  Activity,
  Binary,
  CheckCircle2,
  CircuitBoard,
  Database,
  FileCheck2,
  FlaskConical,
  Gauge,
  LockKeyhole,
  Network,
  RadioTower,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { AssuranceActions } from '@/app/assurance-actions';
import { ControlPlaneSandbox } from '@/app/control-plane-sandbox';
import { EvidenceLanesPanel } from '@/app/evidence-lanes';
import { ModelReplayPanel } from '@/app/model-replay';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

const evidenceRows = [
  {
    claim: 'C-MEASURED',
    question: 'Coexistência WDM altera QBER e SKR?',
    status: 'Suportado, exploratório',
    tone: 'positive',
    decision: 'Suporte dentro da grade',
  },
  {
    claim: 'H-ORIGINAL',
    question: 'WDM prevê desempenho quântico futuro?',
    status: 'Não testado',
    tone: 'warning',
    decision: 'Bloqueado',
  },
  {
    claim: 'L-TRANSPORT',
    question: 'O modelo transporta para potência não vista?',
    status: 'Refutado nos modelos avaliados',
    tone: 'negative',
    decision: 'Nova medição necessária',
  },
  {
    claim: 'B-FIDELITY',
    question: 'WDM prevê fidelidade de Bell medida?',
    status: 'Somente simulação',
    tone: 'neutral',
    decision: 'Não operacional',
  },
];

function StatusDot({ tone }: { tone: string }) {
  return (
    <span aria-hidden="true" className={`status-dot status-dot-${tone}`} />
  );
}

export default function Home() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="assurance-shell">
        <aside className="assurance-sidebar">
          <div className="brand-mark" aria-label="NISQ Predictive Twin">
            <span className="brand-glyph">
              <Binary aria-hidden="true" />
            </span>
            <span>
              <strong>NISQ·PT</strong>
              <small>Quantum control</small>
            </span>
          </div>

          <nav aria-label="Navegação principal" className="side-nav">
            <a className="side-nav-item side-nav-item-active" href="#overview">
              <Gauge aria-hidden="true" />
              Comando
            </a>
            <a className="side-nav-item" href="#sandbox">
              <CircuitBoard aria-hidden="true" />
              Topologia
            </a>
            <a className="side-nav-item" href="#model-replay">
              <Activity aria-hidden="true" />
              Predição WDM
            </a>
            <a className="side-nav-item" href="#lanes">
              <RadioTower aria-hidden="true" />
              Telemetria
            </a>
            <a className="side-nav-item" href="#evidence">
              <FlaskConical aria-hidden="true" />
              Evidências
            </a>
            <a className="side-nav-item" href="#scope">
              <Network aria-hidden="true" />
              Envelope
            </a>
            <a className="side-nav-item" href="#audit">
              <FileCheck2 aria-hidden="true" />
              Auditoria
            </a>
          </nav>

          <div className="sidebar-boundary">
            <LockKeyhole aria-hidden="true" />
            <div>
              <strong>Interlock físico fechado</strong>
              <span>O controlador atua somente na topologia simulada</span>
            </div>
          </div>
        </aside>

        <section className="assurance-workspace">
          <header className="workspace-header">
            <div>
              <p className="eyebrow">NISQ Predictive Twin · v5.34</p>
              <h1>Quantum Network Control Plane</h1>
            </div>
            <AssuranceActions />
          </header>

          <section className="operations-rail" aria-label="Estado do sistema">
            <div className="operations-runtime">
              <span className="rail-dot" aria-hidden="true" />
              <span>
                <small>Runtime local</small>
                <strong>CONTROL CORE</strong>
              </span>
            </div>
            <dl>
              <div>
                <dt>Topologia</dt>
                <dd>sandbox-line-3</dd>
              </div>
              <div>
                <dt>Política</dt>
                <dd>fail-closed</dd>
              </div>
              <div>
                <dt>Modelo</dt>
                <dd>dual-head congelado</dd>
              </div>
              <div>
                <dt>Atuação</dt>
                <dd className="rail-locked">física bloqueada</dd>
              </div>
            </dl>
          </section>

          <div className="workspace-body" id="overview">
            <section className="decision-hero" aria-labelledby="decision-title">
              <div className="decision-signal" aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
              <div className="decision-copy">
                <div className="decision-label">
                  <CheckCircle2 aria-hidden="true" />
                  Control plane operacional
                </div>
                <h2 id="decision-title">
                  Rede simulada sob controle.
                  <span> Atuação física permanece intertravada.</span>
                </h2>
                <p>
                  Execute cenários auditáveis com HALT, WAIT, PURIFY e SWAP,
                  reproduza o modelo WDM congelado em shadow mode e inspecione
                  cada decisão. Um adaptador autenticado de hardware será
                  obrigatório antes de qualquer comando físico.
                </p>
              </div>
              <div className="decision-boundary">
                <span>Capacidade ativa</span>
                <strong>Sandbox + shadow</strong>
                <small>Supervisor independente e journal imutável</small>
              </div>
            </section>

            <section
              className="metrics-grid"
              aria-label="Indicadores de garantia"
            >
              <Card className="metric-card">
                <CardHeader>
                  <CardDescription>Verificações de integridade</CardDescription>
                  <CardAction>
                    <ShieldCheck aria-hidden="true" />
                  </CardAction>
                  <CardTitle className="metric-value">69/69</CardTitle>
                </CardHeader>
                <CardContent>
                  <p>Registro científico e artefatos hash-bound</p>
                </CardContent>
              </Card>
              <Card className="metric-card">
                <CardHeader>
                  <CardDescription>Topologia persistente</CardDescription>
                  <CardAction>
                    <Network aria-hidden="true" />
                  </CardAction>
                  <CardTitle className="metric-value">3 nós</CardTitle>
                </CardHeader>
                <CardContent>
                  <p>Alice · repetidor · Bob, com falhas reproduzíveis</p>
                </CardContent>
              </Card>
              <Card className="metric-card">
                <CardHeader>
                  <CardDescription>Ações supervisionadas</CardDescription>
                  <CardAction>
                    <Activity aria-hidden="true" />
                  </CardAction>
                  <CardTitle className="metric-value">4 ações</CardTitle>
                </CardHeader>
                <CardContent>
                  <p>HALT · WAIT · PURIFY · SWAP</p>
                </CardContent>
              </Card>
              <Card className="metric-card metric-card-caution">
                <CardHeader>
                  <CardDescription>Adaptadores físicos</CardDescription>
                  <CardAction>
                    <TriangleAlert aria-hidden="true" />
                  </CardAction>
                  <CardTitle className="metric-value">0 ativos</CardTitle>
                </CardHeader>
                <CardContent>
                  <p>Fail-closed até API, credenciais e homologação</p>
                </CardContent>
              </Card>
            </section>

            <ControlPlaneSandbox />

            <ModelReplayPanel />

            <EvidenceLanesPanel />

            <div className="content-grid">
              <Card className="evidence-card" id="evidence">
                <CardHeader>
                  <div>
                    <p className="section-kicker">Portfólio de hipóteses</p>
                    <CardTitle>O que pode orientar uma decisão</CardTitle>
                  </div>
                  <CardAction>
                    <Badge variant="secondary">7 reivindicações formais</Badge>
                  </CardAction>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Hipótese</TableHead>
                        <TableHead>Questão de negócio</TableHead>
                        <TableHead>Estado científico</TableHead>
                        <TableHead className="text-right">
                          Uso permitido
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {evidenceRows.map((row) => (
                        <TableRow key={row.claim}>
                          <TableCell className="font-mono text-xs text-cyan-200">
                            {row.claim}
                          </TableCell>
                          <TableCell className="max-w-[270px] whitespace-normal font-medium">
                            {row.question}
                          </TableCell>
                          <TableCell>
                            <span className="status-label">
                              <StatusDot tone={row.tone} />
                              {row.status}
                            </span>
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground">
                            {row.decision}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              <Card className="scope-card" id="scope">
                <CardHeader>
                  <p className="section-kicker">Domínio validado</p>
                  <CardTitle>Envelope IrelandQCI</CardTitle>
                  <CardDescription>
                    Correspondência exata; sem interpolação.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="scope-radar" aria-hidden="true">
                    <span className="scope-ring scope-ring-1" />
                    <span className="scope-ring scope-ring-2" />
                    <span className="scope-ring scope-ring-3" />
                    <span className="scope-core">
                      <RadioTower />
                    </span>
                    <span className="scope-node scope-node-a" />
                    <span className="scope-node scope-node-b" />
                    <span className="scope-node scope-node-c" />
                  </div>
                  <dl className="scope-list">
                    <div>
                      <dt>Fibra</dt>
                      <dd>0 km / 50 km</dd>
                    </div>
                    <div>
                      <dt>Potência injetada</dt>
                      <dd>3 / 7 / 9 / 12 dBm</dd>
                    </div>
                    <div>
                      <dt>Resultados</dt>
                      <dd>QBER + SKR medidos</dd>
                    </div>
                    <div>
                      <dt>Transporte</dt>
                      <dd className="text-amber-300">Não demonstrado</dd>
                    </div>
                  </dl>
                  <Button
                    className="w-full"
                    variant="outline"
                    nativeButton={false}
                    render={
                      <a
                        href="https://doi.org/10.5281/zenodo.21132088"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="Abrir a proveniência IrelandQCI no Zenodo"
                      />
                    }
                  >
                    <Database aria-hidden="true" />
                    Inspecionar proveniência
                  </Button>
                </CardContent>
              </Card>
            </div>

            <section className="audit-strip" id="audit">
              <div>
                <span className="audit-icon">
                  <FileCheck2 aria-hidden="true" />
                </span>
                <div>
                  <strong>Trilha de decisão preservada</strong>
                  <p>
                    Entrada, célula, hashes, violações e ação final em um único
                    registro.
                  </p>
                </div>
              </div>
              <code>qkd-claim-guard-v2 · SHA-256 verificado</code>
            </section>
          </div>
        </section>
      </div>
    </main>
  );
}
