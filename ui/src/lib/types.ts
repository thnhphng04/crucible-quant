/** Shapes returned by `quantcrucible.review.repository`. Every field is read-only by design. */

export interface Snapshot {
  /** When the server read the ledger, not when the research ran. */
  snapshot_at?: string
}

export interface CampaignRow {
  campaign_id: string
  started_at: string
  holdout_range: string
  lock_hash: string | null
  holdout_lock_hash: string | null
  status: string
  purpose: string
  trial_budget: number | null
  abandoned_at: string | null
  abandonment_reason: string | null
  trials?: number
  events?: number
  last_activity?: string | null
}

export interface ProtocolDetail {
  protocol?: { version?: number; [key: string]: unknown }
  sha256?: string
  [key: string]: unknown
}

export interface VerdictSummary {
  outcome: string
  label: string
  reason: string
}

export interface OverviewData extends Snapshot {
  campaign: CampaignRow
  counts: {
    trials: number
    candidates: number
    passed: number | null
    events: number
    warnings: number
  }
  last_activity: string | null
  protocol: ProtocolDetail | null
  verdict: VerdictSummary
}

export interface ComparisonUnit {
  /** The scope this unit searched. `legacy_spot` for a campaign from before P3-12. */
  instrument: string
  direction: string
  /** `instrument-direction-engine-sN`, built once by the API so every screen agrees. */
  unit: string
  engine: string
  seed: number
  trials: number
  passed: number
  quota: number
  pass_rate: number | null
}

export interface CheckpointRow {
  instrument: string | null
  direction: string | null
  engine: string
  seed: number
  ts: string
  detail: {
    candidate_id?: string
    is_sharpe?: number | null
    oos_median?: number | null
    trials?: number
  }
}

export interface ComparisonData extends Snapshot {
  protocol: ProtocolDetail | null
  units: ComparisonUnit[]
  checkpoints: CheckpointRow[]
  warnings: { instrument: string | null; direction: string | null; engine: string; seed: number }[]
}

export interface CandidateRow {
  campaign_id: string
  candidate_id: string
  engine: string
  seed: number
  island: number | string | null
  strategy_hash: string | null
  source: string
  sharpe_is: number | null
  verdict: string
  gate_failed: string | null
  ts: string
  trial_id: number | null
  cell_id: string | null
}

export interface PageData<T> extends Snapshot {
  items: T[]
  page: number
  size: number
  total: number
}

export interface CurvePoint {
  ts: string
  equity: number
  drawdown: number
}

export interface Curve {
  status: 'ok' | 'missing' | 'error'
  message?: string
  points: CurvePoint[]
}

export interface Measurement extends CandidateRow {
  id: number
  universe: string
  timeframe: string
  timerange: string
  params: Record<string, unknown>
  curve: Curve
}

export interface GateRow {
  id: number
  ts: string
  gate: string
  passed: number
  value: number | null
  reason: string | null
  detail: Record<string, unknown> | null
}

export interface CandidateDetail extends Snapshot {
  candidate_id: string
  strategy_hash: string | null
  source: { status: 'ok' | 'missing'; text: string | null }
  submission: { event: string; ts: string; detail: Record<string, unknown> } | null
  measurements: Measurement[]
  gates: GateRow[]
}

export interface PortfolioRow {
  portfolio_hash: string
  campaign_id: string
  rule_config: Record<string, unknown>
  members: Record<string, unknown>[]
  sharpe_is: number | null
  returns_path: string | null
  ts: string
  stale: boolean
}

export interface CalibrationRow {
  campaign_id: string
  candidate_id: string
  strategy_hash: string
  budget: number
  started_at: string
  finished_at: string | null
  outcome: string | null
  attempts: number | null
  trials: number | null
  errors: number | null
}

export interface PortfolioData extends Snapshot {
  items: PortfolioRow[]
  calibrations: CalibrationRow[]
  ledger_trials: number
}

/** One bar of the shared account (P3-23). `residual` is measured, not asserted: the screen shows
 *  it so a drift in the decomposition is visible rather than taken on trust. */
export interface AccountPoint {
  ts: string
  equity: number
  residual: number
}

export interface AccountSlot {
  slot: string
  final: number
  values: number[]
}

export interface AccountData extends Snapshot {
  status: 'ok' | 'missing'
  portfolio_hash: string
  initial_cash: number | null
  points: AccountPoint[]
  slots: AccountSlot[]
  max_residual: number | null
  reconciles: boolean | null
}

export interface ArchiveRow {
  candidate_id: string
  engine: string
  seed: number
  island: number | string | null
  cell_id: string
  sharpe_is: number | null
  verdict: string
  eligible: boolean
}

export interface ArchiveData extends Snapshot {
  items: ArchiveRow[]
  search_cells: number
  eligible_cells: number
}

export interface EventRow {
  id: number
  ts: string
  run_id: string
  campaign_id: string
  engine: string
  seed: number
  agent: string
  model_used: string
  cell_id: string | null
  event: string
  strategy_hash: string | null
  drift_delta: number | null
  island: number | string | null
  detail: Record<string, unknown>
}

export interface EventsData extends PageData<EventRow> {
  /** Every distinct event name in the campaign, so the filter needs no typing. */
  names: string[]
}

export interface LockData extends Snapshot {
  status: 'verified' | 'mismatch' | 'missing'
  lock: Record<string, unknown> | null
  expected_hash: string | null
  actual_hash?: string
}

export interface StudioFieldError {
  path: string
  code: string
  message: string
}

export interface StudioProblem extends StudioFieldError {}

export interface StudioErrorBody {
  code?: string
  message?: string
  fields?: StudioFieldError[]
  detail?: string | { code?: string; message?: string; fields?: StudioFieldError[] }
}

export interface StudioCapabilities {
  contract_version: number
  session_token?: string
  max_workers: number
  docker_available?: boolean
  markets_available?: Record<string, { enabled: boolean; reason?: string }>
  defaults?: StudioDraftConfig
  enums?: {
    purposes?: string[]
    markets?: string[]
    timeframes?: string[]
    engines?: string[]
  }
  ranges?: Record<string, { min?: number; max?: number; step?: number }>
}

export type StudioPurpose = 'research' | 'harness_test'
export type StudioMarket = 'spot' | 'usdt_m_perpetual'
export type StudioJobState =
  | 'queued'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'interrupted'
  | 'unknown'

export interface StudioDraftConfig {
  operational: Record<string, unknown>
  research: {
    max_risk_pct: number
    portfolio?: Record<string, unknown>
    pbo_grid?: Record<string, unknown>
    drift?: Record<string, unknown>
    engines: Record<string, number>
    gp?: Record<string, unknown>
    campaign: {
      purpose: StudioPurpose
      trial_budget: number | null
    }
    engine_mode?: string
    evolve_scope: string
    constraints?: Record<string, unknown>
    seeds: number
    minbtl_target_sharpe?: number
    data: {
      exchange: string
      symbols: string[]
      market: StudioMarket
      timeframe: '15m' | '1h' | string
      start: string
      end: string | null
      holdout_months: number
      second_exchange?: string | null
      leverage?: number
      funding_interval_hours?: number
    }
    exit: {
      tp_sl_ratio: number
      max_holding_bars: number
    }
    calibration?: Record<string, unknown>
    gates?: Record<string, unknown>
    holdout_pass?: number | null
  } & Record<string, unknown>
}

export interface StudioDraft {
  draft_id: string
  revision: number
  config: StudioDraftConfig
  dataset_id: string | null
  campaign_id?: string | null
  state: string
}

export interface StudioJobProgress {
  trials?: number
  quota?: number
  per_arm?: Record<string, { trials: number; quota: number }>
}

export interface StudioJob {
  job_id: string
  state: StudioJobState
  kind: 'prepare_data' | 'evolve' | 'portfolio' | string
  phase?: string | null
  started_at?: string | null
  updated_at?: string | null
  exit_code?: number | null
  progress?: StudioJobProgress
  error_code?: string | null
  log_tail?: string
}

export interface StudioPreview {
  ok: boolean
  problems: StudioProblem[]
  dataset?: {
    id?: string | null
    hash?: string | null
    coverage?: Record<string, unknown> | null
  }
  lock_summary?: Record<string, unknown> | null
  quota?: {
    trials?: number
    per_arm?: Record<string, number>
    [key: string]: unknown
  }
  preview_token?: string
  expires_at?: string
}

export interface StudioCampaignCreated {
  campaign_id: string
  lock_sha256: string
  dataset_id: string
}
