'use client';

import { authenticatedFetch, getApiRoot } from '@/lib/api-session';

import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import {
  ArrowUpRight,
  CheckCircle2,
  Download,
  FileJson2,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  Upload,
  WifiOff,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';

const API_ROOT = getApiRoot();
const MAX_IMPORT_BYTES = 256 * 1024;

interface CampaignSummary {
  campaign_id: string;
  fiber_length_km: number;
  external_signal_present: boolean;
  external_signal_power_dbm: number | null;
  voa_attenuation_db: number[];
}

interface AssuranceSummary {
  integrity: {
    valid: boolean;
    check_count: number;
    failed_check_count: number;
  };
  operating_grid: {
    dataset_id: string;
    expected_cell_count: number;
    campaigns: CampaignSummary[];
  };
}

interface DomainViolation {
  reason_code: string;
  constraint: string;
  detail: string;
}

interface DecisionResponse {
  api_version: string;
  generated_at: string;
  source_cell?: {
    qber_reported: number;
    qber_unit_status: string;
    secure_key_rate_bps: number;
    n_measurements: number;
    qber_reported_sd: number;
    secure_key_rate_sd: number;
  };
  record: {
    decision: {
      action: string;
      reason_codes: string[];
    };
    evaluated_candidates: Array<{
      eligible: boolean;
      domain_violations: DomainViolation[];
    }>;
    snapshot: {
      observed: {
        condition_scope: string;
        grid_classification: {
          matched_cell: { cell_id: string } | null;
        };
      };
    };
    provenance: {
      config_hash: string;
      artifact_hashes: Record<string, string>;
    };
  };
}

function campaignLabel(campaign: CampaignSummary) {
  const condition = campaign.external_signal_present
    ? `${campaign.external_signal_power_dbm} dBm WDM`
    : 'baseline sem WDM';
  return `${campaign.fiber_length_km} km · ${condition}`;
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

export function AssuranceActions() {
  const [summary, setSummary] = useState<AssuranceSummary | null>(null);
  const [campaignId, setCampaignId] = useState('');
  const [voa, setVoa] = useState('');
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [apiState, setApiState] = useState<
    'loading' | 'online' | 'invalid' | 'offline'
  >('loading');

  useEffect(() => {
    let active = true;
    requestJson<AssuranceSummary>('/summary')
      .then((payload) => {
        if (!active) return;
        setSummary(payload);
        const defaultCampaign =
          payload.operating_grid.campaigns.find(
            (item) =>
              item.external_signal_present &&
              item.external_signal_power_dbm === 3,
          ) ?? payload.operating_grid.campaigns[0];
        if (defaultCampaign) {
          setCampaignId(defaultCampaign.campaign_id);
          setVoa(String(defaultCampaign.voa_attenuation_db[0]));
        }
        setApiState(payload.integrity.valid ? 'online' : 'invalid');
      })
      .catch(() => {
        if (active) setApiState('offline');
      });
    return () => {
      active = false;
    };
  }, []);

  const campaign = useMemo(
    () =>
      summary?.operating_grid.campaigns.find(
        (item) => item.campaign_id === campaignId,
      ),
    [campaignId, summary],
  );

  function selectCampaign(value: string | null) {
    if (!value || !summary) return;
    const next = summary.operating_grid.campaigns.find(
      (item) => item.campaign_id === value,
    );
    setCampaignId(value);
    setVoa(next ? String(next.voa_attenuation_db[0]) : '');
    setDecision(null);
    setError('');
  }

  async function assessPublishedCell() {
    if (!campaign || voa === '') return;
    setBusy(true);
    setError('');
    try {
      const result = await requestJson<DecisionResponse>('/assess-cell', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaign.campaign_id,
          voa_attenuation_db: Number(voa),
        }),
      });
      setDecision(result);
    } catch (reason) {
      setDecision(null);
      setError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível avaliar a célula.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function importOperatingPoint(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > MAX_IMPORT_BYTES) {
      setError('O arquivo excede o limite local de 256 KiB.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      const operatingPoint = parsed.operating_point ?? parsed;
      const result = await requestJson<DecisionResponse>('/assess', {
        method: 'POST',
        body: JSON.stringify({
          claim_id: 'C-MEASURED',
          requested_use: 'decision_support',
          operating_point: operatingPoint,
        }),
      });
      setDecision(result);
    } catch (reason) {
      setDecision(null);
      setError(
        reason instanceof Error
          ? reason.message
          : 'O JSON não contém um ponto operacional válido.',
      );
    } finally {
      setBusy(false);
    }
  }

  function exportRecord() {
    if (!decision) return;
    const blob = new Blob([JSON.stringify(decision, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `wdm-qkd-decision-${Date.now()}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const actionAllowed =
    decision?.record.decision.action === 'PROVIDE_DECISION_SUPPORT';
  const candidate = decision?.record.evaluated_candidates[0];
  const scope = decision?.record.snapshot.observed.condition_scope;

  return (
    <div className="header-actions">
      <Badge
        className={`verified-badge ${
          apiState === 'offline' || apiState === 'invalid'
            ? 'verified-badge-offline'
            : ''
        }`}
        variant="outline"
      >
        {apiState === 'online' ? (
          <ShieldCheck aria-hidden="true" />
        ) : apiState === 'offline' ? (
          <WifiOff aria-hidden="true" />
        ) : apiState === 'invalid' ? (
          <ShieldAlert aria-hidden="true" />
        ) : (
          <Loader2 className="animate-spin" aria-hidden="true" />
        )}
        {apiState === 'online'
          ? `${
              (summary?.integrity.check_count ?? 0) -
              (summary?.integrity.failed_check_count ?? 0)
            }/${summary?.integrity.check_count} verificações`
          : apiState === 'invalid'
            ? 'Integridade bloqueada'
            : apiState === 'offline'
              ? 'API local offline'
              : 'Verificando'}
      </Badge>

      <Sheet>
        <SheetTrigger
          render={
            <Button
              className="primary-action"
              size="lg"
              disabled={apiState !== 'online'}
            />
          }
        >
          Avaliar condição
          <ArrowUpRight data-icon="inline-end" aria-hidden="true" />
        </SheetTrigger>
        <SheetContent className="assurance-sheet sm:max-w-xl">
          <SheetHeader className="assurance-sheet-header">
            <p className="section-kicker">Decisão reproduzível</p>
            <SheetTitle>Avaliar um ponto operacional</SheetTitle>
            <SheetDescription>
              Selecione uma célula publicada ou importe telemetria tipada. O
              backend valida escopo, hashes e política antes de liberar suporte
              à decisão.
            </SheetDescription>
          </SheetHeader>

          <div className="assurance-sheet-body">
            <Alert className="safety-alert">
              <ShieldAlert aria-hidden="true" />
              <AlertTitle>Guardrail científico ativo</AlertTitle>
              <AlertDescription>
                Esta versão controla somente a topologia simulada. Atuação
                física, interpolação, transferência entre plataformas e
                alegações de predição temporal permanecem bloqueadas.
              </AlertDescription>
            </Alert>

            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="campaign">
                  Campanha experimental
                </FieldLabel>
                <Select value={campaignId} onValueChange={selectCampaign}>
                  <SelectTrigger id="campaign" className="w-full">
                    <SelectValue placeholder="Selecione a campanha" />
                  </SelectTrigger>
                  <SelectContent>
                    {summary?.operating_grid.campaigns.map((item) => (
                      <SelectItem
                        key={item.campaign_id}
                        value={item.campaign_id}
                      >
                        {campaignLabel(item)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FieldDescription>
                  Dataset IrelandQCI com artefato e configuração vinculados por
                  SHA-256.
                </FieldDescription>
              </Field>

              <Field>
                <FieldLabel htmlFor="voa">Atenuação VOA</FieldLabel>
                <Select
                  value={voa}
                  onValueChange={(value) => value && setVoa(value)}
                >
                  <SelectTrigger id="voa" className="w-full">
                    <SelectValue placeholder="Selecione o ponto medido" />
                  </SelectTrigger>
                  <SelectContent>
                    {campaign?.voa_attenuation_db.map((value) => (
                      <SelectItem key={value} value={String(value)}>
                        {value} dB
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FieldDescription>
                  QBER e SKR serão carregados do CSV verificado, nunca
                  inventados no navegador.
                </FieldDescription>
              </Field>

              <Button
                onClick={assessPublishedCell}
                disabled={busy || !campaign || voa === ''}
              >
                {busy ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <ShieldCheck />
                )}
                Verificar célula e emitir registro
              </Button>
            </FieldGroup>

            <div className="import-panel">
              <div>
                <strong>Telemetria externa tipada</strong>
                <span>JSON de QKDOperatingPoint · limite 256 KiB</span>
              </div>
              <Button
                variant="outline"
                size="sm"
                render={
                  <label
                    htmlFor="point-file"
                    aria-label="Importar ponto operacional JSON"
                  />
                }
              >
                <Upload aria-hidden="true" />
                Importar JSON
              </Button>
              <Input
                className="sr-only"
                id="point-file"
                type="file"
                accept="application/json,.json"
                onChange={importOperatingPoint}
              />
            </div>

            {error && (
              <Alert variant="destructive">
                <ShieldAlert aria-hidden="true" />
                <AlertTitle>Avaliação bloqueada</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {decision && (
              <section
                className={`decision-result ${actionAllowed ? 'decision-result-pass' : ''}`}
              >
                <div className="decision-result-head">
                  <span className="decision-result-icon">
                    {actionAllowed ? <CheckCircle2 /> : <ShieldAlert />}
                  </span>
                  <div>
                    <small>
                      {actionAllowed ? 'SUPORTE LIBERADO' : 'DECISÃO BLOQUEADA'}
                    </small>
                    <strong>
                      {actionAllowed
                        ? 'Dentro do envelope validado'
                        : 'Fora do uso autorizado'}
                    </strong>
                  </div>
                </div>

                <dl className="decision-result-grid">
                  <div>
                    <dt>Ação</dt>
                    <dd>{decision.record.decision.action}</dd>
                  </div>
                  <div>
                    <dt>Escopo</dt>
                    <dd>{scope}</dd>
                  </div>
                  {decision.source_cell && (
                    <>
                      <div>
                        <dt>QBER publicado*</dt>
                        <dd>
                          {decision.source_cell.qber_reported.toPrecision(5)}
                        </dd>
                      </div>
                      <div>
                        <dt>SKR publicado</dt>
                        <dd>
                          {new Intl.NumberFormat('pt-BR', {
                            maximumFractionDigits: 0,
                          }).format(
                            decision.source_cell.secure_key_rate_bps,
                          )}{' '}
                          bit/s
                        </dd>
                      </div>
                      <div>
                        <dt>Amostras da célula</dt>
                        <dd>{decision.source_cell.n_measurements}</dd>
                      </div>
                      <div>
                        <dt>Escala QBER</dt>
                        <dd>{decision.source_cell.qber_unit_status}</dd>
                      </div>
                    </>
                  )}
                </dl>

                {candidate && candidate.domain_violations.length > 0 && (
                  <ul className="violation-list">
                    {candidate.domain_violations.map((violation) => (
                      <li
                        key={`${violation.reason_code}-${violation.constraint}`}
                      >
                        <strong>{violation.reason_code}</strong>
                        <span>{violation.detail}</span>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="decision-audit-meta">
                  <FileJson2 aria-hidden="true" />
                  <span>
                    {decision.record.snapshot.observed.grid_classification
                      .matched_cell?.cell_id ??
                      'sem célula publicada correspondente'}
                  </span>
                  <code>
                    {decision.record.provenance.config_hash.slice(0, 12)}…
                  </code>
                </div>
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={exportRecord}
                >
                  <Download aria-hidden="true" />
                  Exportar trilha de decisão JSON
                </Button>
              </section>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
