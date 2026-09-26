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
