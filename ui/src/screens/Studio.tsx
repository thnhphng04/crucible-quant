import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import s from '../ui.module.css'
import { ApiError, getJson, sendJson, useApi } from '../lib/api'
import { dateTime, shortHash } from '../lib/format'
import type {
  CampaignRow,
  StudioCampaignCreated,
  StudioCapabilities,
  StudioDraft,
  StudioDraftConfig,
  StudioFieldError,
  StudioJob,
  StudioPreview,
} from '../lib/types'
import { Metric, MetricGrid, Panel } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

const LAST_DRAFT = 'quantcrucible.studio.draft'
const LAST_JOB = 'quantcrucible.studio.job'
const STEPS = ['Mục đích', 'Dữ liệu', 'Quy tắc', 'Xem trước'] as const

function key(kind: string): string {
  return `${kind}-${crypto.randomUUID()}`
}

function numeric(value: unknown, fallback: number): number {
  return typeof value === 'number' ? value : fallback
}

function failure(error: unknown): { message: string; fields: StudioFieldError[] } {
  if (error instanceof ApiError) return { message: error.message, fields: error.fields }
  return { message: error instanceof Error ? error.message : String(error), fields: [] }
}

function JobPanel({
  jobId,
  token,
  onFinished,
}: {
  jobId: string
  token: string
  onFinished: (job: StudioJob) => void
}) {
  const { data, error, loading, reload } = useApi<StudioJob>(`/api/studio/jobs/${jobId}`)
  const [notified, setNotified] = useState(false)

  useEffect(() => {
    if (data && !notified && ['succeeded', 'failed', 'interrupted'].includes(data.state)) {
      setNotified(true)
      onFinished(data)
    }
  }, [data, notified, onFinished])

  async function stop() {
    try {
      await sendJson<StudioJob>({
        url: `/api/studio/jobs/${jobId}/stop`,
        token,
        body: { request_key: key('stop') },
      })
      reload()
    } catch {
      reload()
    }
  }

  if (loading && !data) return <Loading text="Đang đọc job…" />
  if (!data) return <ErrorBox text={error || 'Không tìm thấy job'} onRetry={reload} />
  const trials = data.progress?.trials ?? 0
  const quota = data.progress?.quota ?? 0
  return (
    <Panel title="Job" note="Trạng thái tiến trình tách riêng với trạng thái OPEN của campaign.">
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      <MetricGrid>
        <Metric label="Loại" value={data.kind} raw />
        <Metric label="Trạng thái" value={data.state} raw />
        <Metric label="Cập nhật" value={dateTime(data.updated_at)} raw />
        <Metric label="Trial" value={quota ? `${trials}/${quota}` : 'Chưa có'} raw />
      </MetricGrid>
      {quota ? (
        <div className={s.progress} role="img" aria-label={`Đã ghi ${trials} trên ${quota} trial`}>
          <span style={{ width: `${Math.min(100, (trials / quota) * 100)}%` }} />
        </div>
      ) : null}
      {data.log_tail ? <pre className={s.code}>{data.log_tail}</pre> : null}
      {['queued', 'starting', 'running'].includes(data.state) ? (
        <button className={s.button} type="button" onClick={stop}>Dừng job</button>
      ) : null}
    </Panel>
  )
}

export function Studio({
  campaigns,
  onCreated,
}: {
  campaigns: CampaignRow[]
  onCreated?: () => void
}) {
  const [capabilities, setCapabilities] = useState<StudioCapabilities | null>(null)
  const [unavailable, setUnavailable] = useState('')
  const [config, setConfig] = useState<StudioDraftConfig | null>(null)
  const [draft, setDraft] = useState<StudioDraft | null>(null)
  const [dirty, setDirty] = useState(false)
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [fields, setFields] = useState<StudioFieldError[]>([])
  const [preview, setPreview] = useState<StudioPreview | null>(null)
  const [created, setCreated] = useState<StudioCampaignCreated | null>(null)
  const [jobId, setJobId] = useState<string | null>(localStorage.getItem(LAST_JOB))
  const [workers, setWorkers] = useState(8)
  const [abandonId, setAbandonId] = useState('')
  const [abandonReason, setAbandonReason] = useState('')

  useEffect(() => {
    let active = true
    getJson<StudioCapabilities>('/api/studio/capabilities')
      .then(async (value) => {
        if (!active) return
        setCapabilities(value)
        if (value.defaults) setConfig(value.defaults)
        const last = localStorage.getItem(LAST_DRAFT)
        if (!last) return
        try {
          const stored = await getJson<StudioDraft>(`/api/studio/drafts/${last}`)
          if (active) {
            setDraft(stored)
            setConfig(stored.config)
            if (stored.state === 'created' && stored.campaign_id && stored.dataset_id) {
              setCreated({
                campaign_id: stored.campaign_id,
                dataset_id: stored.dataset_id,
                lock_sha256: '',
              })
            }
          }
        } catch {
          localStorage.removeItem(LAST_DRAFT)
        }
      })
      .catch((error: unknown) => {
        if (active) setUnavailable(failure(error).message)
      })
    return () => { active = false }
  }, [])

  const token = capabilities?.session_token ?? ''
  const openCampaign = campaigns.find((item) => item.status === 'OPEN')

  function change(next: StudioDraftConfig) {
    if (created || draft?.state === 'created') return
    setConfig(next)
    setDirty(true)
    setPreview(null)
    setFields([])
    setMessage('')
  }

  function research(patch: Partial<StudioDraftConfig['research']>) {
    if (config) change({ ...config, research: { ...config.research, ...patch } })
  }

  function data(patch: Partial<StudioDraftConfig['research']['data']>) {
    if (config) research({ data: { ...config.research.data, ...patch } })
  }

  async function mutation<T>(url: string, body: object, method: 'POST' | 'PATCH' = 'POST') {
    return sendJson<T>({ url, body, token, method })
  }

  async function save() {
    if (!config) return
    setBusy('save')
    setFields([])
    try {
      const next = draft
        ? await sendJson<StudioDraft>({
            url: `/api/studio/drafts/${draft.draft_id}`,
            body: { config },
            token,
            method: 'PATCH',
            headers: { 'if-match': `"${draft.revision}"` },
          })
        : await mutation<StudioDraft>('/api/studio/drafts', { config })
      setDraft(next)
      setConfig(next.config)
      setDirty(false)
      setPreview(null)
      localStorage.setItem(LAST_DRAFT, next.draft_id)
      setMessage(`Draft revision ${next.revision} đã lưu.`)
    } catch (error) {
      const result = failure(error)
      setMessage(result.message)
      setFields(result.fields)
    } finally {
      setBusy('')
    }
  }

  async function prepare() {
    if (!draft || dirty) return
    setBusy('prepare')
    try {
      const job = await mutation<StudioJob>(`/api/studio/drafts/${draft.draft_id}/prepare-data`, {
        draft_revision: draft.revision,
        request_key: key('prepare'),
      })
      setJobId(job.job_id)
      localStorage.setItem(LAST_JOB, job.job_id)
      setMessage('Đang chuẩn bị dữ liệu trong job riêng.')
    } catch (error) {
      setMessage(failure(error).message)
    } finally {
      setBusy('')
    }
  }

  async function reviewPreview() {
    if (!draft || dirty) return
    setBusy('preview')
    try {
      const result = await mutation<StudioPreview>(`/api/studio/drafts/${draft.draft_id}/preview`, {
        draft_revision: draft.revision,
      })
      setPreview(result)
      setFields(result.problems)
      setMessage(result.ok ? 'Preview hợp lệ.' : 'Preview còn lỗi cần xử lý.')
    } catch (error) {
      const result = failure(error)
      setMessage(result.message)
      setFields(result.fields)
    } finally {
      setBusy('')
    }
  }

  async function createCampaign() {
    if (!draft || !preview?.preview_token || dirty) return
    setBusy('create')
    try {
      const result = await mutation<StudioCampaignCreated>('/api/studio/campaigns', {
        draft_id: draft.draft_id,
        draft_revision: draft.revision,
        preview_token: preview.preview_token,
        request_key: `${draft.draft_id}-create`,
      })
      setCreated(result)
      setMessage(`Campaign ${result.campaign_id} đã tạo.`)
      onCreated?.()
    } catch (error) {
      setMessage(failure(error).message)
    } finally {
      setBusy('')
    }
  }

  async function run(kind: 'runs' | 'portfolio') {
    if (!created || !config) return
    setBusy(kind)
    const body = kind === 'runs'
      ? {
          engines: (['gp', 'random'] as const).filter((engine) => config.research.engines[engine] > 0),
          workers,
          request_key: key('run'),
        }
      : { request_key: key('portfolio') }
    try {
      const job = await mutation<StudioJob>(
        `/api/studio/campaigns/${created.campaign_id}/${kind}`, body,
      )
      setJobId(job.job_id)
      localStorage.setItem(LAST_JOB, job.job_id)
      setMessage(`${kind === 'runs' ? 'Search' : 'Portfolio'} job đã bắt đầu.`)
    } catch (error) {
      setMessage(failure(error).message)
    } finally {
      setBusy('')
    }
  }

  async function abandon() {
    if (!openCampaign || abandonId !== openCampaign.campaign_id || !abandonReason.trim()) return
    setBusy('abandon')
    try {
      await mutation(`/api/studio/campaigns/${openCampaign.campaign_id}/abandon`, {
        campaign_id: abandonId,
        reason: abandonReason,
        request_key: key('abandon'),
      })
      setMessage(`Campaign ${openCampaign.campaign_id} đã ABANDONED; trial vẫn tính vào N.`)
      onCreated?.()
    } catch (error) {
      setMessage(failure(error).message)
    } finally {
      setBusy('')
    }
  }

  async function jobFinished(job: StudioJob) {
    if (job.kind === 'prepare_data' && job.state === 'succeeded' && draft) {
      try {
        const fresh = await getJson<StudioDraft>(`/api/studio/drafts/${draft.draft_id}`)
        setDraft(fresh)
        setConfig(fresh.config)
        setMessage(`Dataset ${fresh.dataset_id ?? ''} đã sẵn sàng.`)
      } catch (error) {
        setMessage(failure(error).message)
      }
    }
  }

  function newDraft() {
    if (!capabilities?.defaults) return
    localStorage.removeItem(LAST_DRAFT)
    setDraft(null)
    setCreated(null)
    setConfig(capabilities.defaults)
    setPreview(null)
    setFields([])
    setDirty(false)
    setStep(0)
    setMessage('Đang cấu hình draft mới.')
  }

  if (unavailable) return (
    <>
      <h1>Campaign Studio</h1>
      <ErrorBox text={`Studio API chưa khả dụng (${unavailable}). Mở bằng cli studio.`} />
      <Panel title="Campaign lịch sử">
        {campaigns.map((item) => <p key={item.campaign_id}>
          <Link to={`/campaign/${item.campaign_id}/overview`}>{item.campaign_id}</Link>
        </p>)}
      </Panel>
    </>
  )
  if (!capabilities || !config) return <Loading text="Đang mở Studio…" />

  const r = config.research
  const d = r.data
  const blocked = Boolean(busy)
  const saved = Boolean(draft && !dirty)
  const options = capabilities.enums
  return (
    <>
      <header className={s.top}>
        <div>
          <h1>Campaign Studio</h1>
          <p className={s.muted}>Cấu hình, tạo và chạy campaign trên máy này.</p>
        </div>
        <button className={s.button} type="button" onClick={newDraft}>Draft mới</button>
      </header>
      <MetricGrid>
        <Metric label="Campaign OPEN" value={openCampaign?.campaign_id ?? 'Không có'} raw />
        <Metric label="Draft" value={draft ? `r${draft.revision}` : 'Chưa lưu'} raw />
        <Metric label="Dataset" value={draft?.dataset_id ?? 'Chưa chuẩn bị'} raw />
      </MetricGrid>
      {message ? <p role="status" className={fields.length ? s.error : s.notice}>{message}</p> : null}
      {fields.length ? <div role="alert" className={s.error}>
        {fields.map((item, index) => <p key={`${item.path}-${index}`}>
          {item.path}: {item.message}
        </p>)}
      </div> : null}
      {jobId ? <JobPanel jobId={jobId} token={token} onFinished={jobFinished} /> : null}

      <section className={s.studioLayout}>
        <nav className={s.stepper} aria-label="Các bước tạo campaign">
          {STEPS.map((name, index) => <button key={name} type="button"
            className={step === index ? s.stepActive : ''} onClick={() => setStep(index)}>
            <span>{index + 1}</span>{name}
          </button>)}
        </nav>
        <div className={s.panel}>
          <h2>{STEPS[step]}</h2>
          <fieldset className={s.fieldset} disabled={Boolean(created || draft?.state === 'created')}>
          {step === 0 ? <div className={s.formGrid}>
            <label className={s.field}>Purpose
              <select value={r.campaign.purpose} onChange={(event) =>
                research({ campaign: { ...r.campaign, purpose: event.target.value as typeof r.campaign.purpose } })}>
                {(options?.purposes ?? ['research', 'harness_test']).map((value) =>
                  <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <p className={s.muted}>Harness khóa protocol GP ↔ Random trước trial đầu. Research có bước đánh giá danh mục sau search.</p>
          </div> : null}
          {step === 1 ? <div className={s.formGrid}>
            <label className={s.field}>Market
              <select value={d.market} onChange={(event) => data({ market: event.target.value as typeof d.market })}>
                {(options?.markets ?? ['spot', 'usdt_m_perpetual']).map((value) =>
                  <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <label className={s.field}>Exchange chính
              <input value={d.exchange} onChange={(event) => data({ exchange: event.target.value })} />
            </label>
            <label className={s.field}>Exchange thứ hai
              <input value={d.second_exchange ?? ''} onChange={(event) =>
                data({ second_exchange: event.target.value || null })} />
            </label>
            <label className={s.field}>Symbols, cách nhau bằng dấu phẩy
              <input value={d.symbols.join(', ')} onChange={(event) => data({
                symbols: event.target.value.split(',').map((item) => item.trim()).filter(Boolean),
              })} />
            </label>
            <label className={s.field}>Timeframe
              <select value={d.timeframe} onChange={(event) => data({ timeframe: event.target.value })}>
                {(options?.timeframes ?? ['15m', '1h']).map((value) =>
                  <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <label className={s.field}>Start UTC
              <input type="date" value={d.start} onChange={(event) => data({ start: event.target.value })} />
            </label>
            <label className={s.field}>End UTC (để trống để chốt khi fetch)
              <input type="date" value={d.end ?? ''} onChange={(event) => data({ end: event.target.value || null })} />
            </label>
            <label className={s.field}>Holdout, tháng
              <input type="number" min={1} value={d.holdout_months} onChange={(event) =>
                data({ holdout_months: Number(event.target.value) })} />
            </label>
            {d.market === 'usdt_m_perpetual' ? <>
              <label className={s.field}>Đòn bẩy tối đa
                <input type="number" min={1} value={d.leverage ?? 5} onChange={(event) =>
                  data({ leverage: Number(event.target.value) })} />
              </label>
              <label className={s.field}>Chu kỳ funding, giờ
                <input type="number" min={1} value={d.funding_interval_hours ?? 8} onChange={(event) =>
                  data({ funding_interval_hours: Number(event.target.value) })} />
              </label>
            </> : null}
          </div> : null}
          {step === 2 ? <div>
            <div className={s.formGrid}>
            <label className={s.field}>Ngân sách trial
              <input type="number" min={1} value={r.campaign.trial_budget ?? ''} onChange={(event) =>
                research({ campaign: { ...r.campaign, trial_budget: Number(event.target.value) } })} />
            </label>
            <label className={s.field}>GP share
              <input type="number" min={0} max={1} step={0.05} value={r.engines.gp} onChange={(event) => {
                const gp = Number(event.target.value)
                research({ engines: { ...r.engines, gp, random: Math.max(0, 1 - gp) } })
              }} />
            </label>
            <label className={s.field}>Seeds
              <input type="number" min={1} value={r.seeds} onChange={(event) =>
                research({ seeds: Number(event.target.value) })} />
            </label>
            <label className={s.field}>Rủi ro tại SL, tỷ lệ vốn
              <input type="number" min={0.001} max={1} step={0.001} value={r.max_risk_pct} onChange={(event) =>
                research({ max_risk_pct: Number(event.target.value) })} />
            </label>
            <label className={s.field}>TP / SL
              <input type="number" min={0.01} step={0.1} value={r.exit.tp_sl_ratio} onChange={(event) =>
                research({ exit: { ...r.exit, tp_sl_ratio: Number(event.target.value) } })} />
            </label>
            <label className={s.field}>Giữ tối đa, bar
              <input type="number" min={1} value={r.exit.max_holding_bars} onChange={(event) =>
                research({ exit: { ...r.exit, max_holding_bars: Number(event.target.value) } })} />
            </label>
            <label className={s.field}>Precision backtest (D23)
              <select value={r.backtest?.precision ?? 'float64'} onChange={(event) =>
                research({ backtest: { precision: event.target.value as 'float64' | 'float32' } })}>
                <option value="float64">float64 — khớp từng bit với replay chuẩn</option>
                <option value="float32">float32 — nhanh hơn, chỉ nhận genome engine C</option>
              </select>
            </label>
            <label className={s.field}>Ngưỡng Sharpe holdout D4
              <input type="number" min={0.01} step={0.1} value={r.holdout_pass ?? ''} onChange={(event) =>
                research({ holdout_pass: event.target.value ? Number(event.target.value) : null })} />
            </label>
            <p className={s.muted}>Stop của từng strategy do bộ sinh chọn theo ATR hoặc Bollinger; khoảng TP và thời gian giữ được khóa cho cả campaign.</p>
            </div>
            <details>
              <summary>Cài đặt nghiên cứu nâng cao</summary>
              <div className={s.formGrid}>
                <label className={s.field}>Vùng tiến hóa
                  <select value={r.evolve_scope} onChange={(event) =>
                    research({ evolve_scope: event.target.value })}>
                    {['entry', 'exit', 'regime', 'joint'].map((value) =>
                      <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className={s.field}>Chế độ engine
                  <select value={r.engine_mode ?? 'isolated'} onChange={(event) =>
                    research({ engine_mode: event.target.value })}>
                    <option value="isolated">isolated</option>
                    <option value="collaborative">collaborative</option>
                  </select>
                </label>
                <label className={s.field}>Min trades
                  <input type="number" min={1} value={numeric(r.constraints?.min_trades, 30)} onChange={(event) =>
                    research({ constraints: { ...r.constraints, min_trades: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Min holding bars
                  <input type="number" min={1} value={numeric(r.constraints?.min_holding_bars, 1)} onChange={(event) =>
                    research({ constraints: { ...r.constraints, min_holding_bars: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Indicator correlation tối đa
                  <input type="number" min={0.01} max={1} step={0.05} value={numeric(r.constraints?.max_indicator_corr, 0.9)} onChange={(event) =>
                    research({ constraints: { ...r.constraints, max_indicator_corr: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>DSR tối thiểu
                  <input type="number" min={0.95} max={0.999} step={0.01} value={numeric(r.gates?.dsr_min, 0.95)} onChange={(event) =>
                    research({ gates: { ...r.gates, dsr_min: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>PBO tối đa
                  <input type="number" min={0.01} max={0.5} step={0.05} value={numeric(r.gates?.pbo_max, 0.5)} onChange={(event) =>
                    research({ gates: { ...r.gates, pbo_max: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Mục tiêu Sharpe MinBTL
                  <input type="number" min={0.1} max={1.5} step={0.1} value={r.minbtl_target_sharpe ?? 1.5} onChange={(event) =>
                    research({ minbtl_target_sharpe: Number(event.target.value) })} />
                </label>
                <label className={s.field}>PBO values/parameter
                  <input type="number" min={1} value={numeric(r.pbo_grid?.values_per_param, 5)} onChange={(event) =>
                    research({ pbo_grid: { ...r.pbo_grid, values_per_param: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>PBO max configs
                  <input type="number" min={1} value={numeric(r.pbo_grid?.max_configs, 200)} onChange={(event) =>
                    research({ pbo_grid: { ...r.pbo_grid, max_configs: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Số strategy tối đa trong danh mục
                  <input type="number" min={1} value={numeric(r.portfolio?.max_strategies, 20)} onChange={(event) =>
                    research({ portfolio: { ...r.portfolio, max_strategies: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Portfolio correlation tối đa
                  <input type="number" min={0.01} max={1} step={0.05} value={numeric(r.portfolio?.max_corr, 0.5)} onChange={(event) =>
                    research({ portfolio: { ...r.portfolio, max_corr: Number(event.target.value) } })} />
                </label>
                <label className={s.field}>Rebalance
                  <select value={String(r.portfolio?.rebalance ?? 'monthly')} onChange={(event) =>
                    research({ portfolio: { ...r.portfolio, rebalance: event.target.value } })}>
                    {['weekly', 'monthly', 'quarterly'].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className={s.field}>Calibration budget/strategy
                  <input type="number" min={1} value={numeric(r.calibration?.budget_per_strategy, 50)} onChange={(event) =>
                    research({ calibration: { ...r.calibration, budget_per_strategy: Number(event.target.value) } })} />
                </label>
              </div>
            </details>
          </div> : null}
          {step === 3 ? <div className={s.summaryGrid}>
            <p>Purpose: {r.campaign.purpose}</p>
            <p>Dữ liệu: {d.market}, {d.timeframe}, {d.start} → {d.end ?? 'ngày UTC mới nhất'}</p>
            <p>Symbols: {d.symbols.join(', ')}</p>
            <p>Budget: {r.campaign.trial_budget ?? 'chưa đặt'} trial; GP {r.engines.gp}, Random {r.engines.random}</p>
            <p>SL/TP: {r.exit.tp_sl_ratio}; giữ tối đa {r.exit.max_holding_bars} bar</p>
            <p>Precision: {r.backtest?.precision ?? 'float64'} (khóa theo campaign)</p>
            <p>Dataset: {draft?.dataset_id ?? 'chưa chuẩn bị'}</p>
            <p>Preview: {preview?.ok ? 'hợp lệ' : preview ? `${preview.problems.length} lỗi` : 'chưa chạy'}</p>
          </div> : null}
          </fieldset>
          <div className={s.actionBar}>
            <button className={s.button} type="button" disabled={step === 0} onClick={() => setStep(step - 1)}>Quay lại</button>
            <button className={s.button} type="button" disabled={step === 3} onClick={() => setStep(step + 1)}>Tiếp</button>
            <button className={s.button} type="button" disabled={blocked || Boolean(created || draft?.state === 'created')} onClick={save}>
              {draft ? 'Lưu draft' : 'Tạo draft'}
            </button>
            <button className={s.button} type="button" disabled={!saved || blocked || Boolean(created)} onClick={prepare}>Chuẩn bị dữ liệu</button>
            <button className={s.button} type="button" disabled={!saved || blocked || Boolean(created)} onClick={reviewPreview}>Xem trước</button>
            <button className={s.button} type="button" disabled={!preview?.ok || blocked || Boolean(created)} onClick={createCampaign}>Tạo campaign</button>
          </div>
        </div>
      </section>

      <section className={s.split}>
        <Panel title="Preview">
          {preview ? <>
            <MetricGrid>
              <Metric label="Kết quả" value={preview.ok ? 'Hợp lệ' : 'Có lỗi'} raw />
              <Metric label="Dataset" value={preview.dataset?.id ?? 'Chưa có'} raw />
              <Metric label="Hash" value={shortHash(preview.dataset?.hash, 16)} raw />
              <Metric label="Hết hạn" value={dateTime(preview.expires_at)} raw />
            </MetricGrid>
            {preview.dataset?.coverage && Object.keys(preview.dataset.coverage).length ? (
              <pre className={s.code}>{JSON.stringify(preview.dataset.coverage, null, 2)}</pre>
            ) : null}
            {preview.lock_summary ? <pre className={s.code}>{JSON.stringify(preview.lock_summary, null, 2)}</pre> : null}
          </> : <Empty text="Preview không ghi ledger hoặc lock." />}
        </Panel>
        <Panel title="Campaign và run">
          {created ? <>
            <p>Campaign <strong>{created.campaign_id}</strong> đã tạo với dataset {created.dataset_id}.</p>
            <label className={s.field}>Workers
              <input type="number" min={1} max={capabilities.max_workers} value={workers} onChange={(event) =>
                setWorkers(Number(event.target.value))} />
            </label>
            <div className={s.actionBar}>
              <button className={s.button} disabled={blocked} onClick={() => run('runs')}>Chạy campaign</button>
              {r.campaign.purpose === 'research' ? <button className={s.button} disabled={blocked}
                onClick={() => run('portfolio')}>Đánh giá danh mục</button> : null}
              <Link to={`/campaign/${created.campaign_id}/overview`}>Mở review</Link>
            </div>
          </> : <Empty text="Tạo campaign sau khi preview hợp lệ." />}
        </Panel>
      </section>
      {openCampaign ? <Panel title="Campaign đang OPEN"
        note="Chỉ dùng khi bạn quyết định đóng campaign này. Trial cũ vẫn tính vào N; thao tác không đảo ngược.">
        <p>{openCampaign.campaign_id} · {openCampaign.trials} trial</p>
        <label className={s.field}>Nhập đúng campaign ID
          <input value={abandonId} onChange={(event) => setAbandonId(event.target.value)} />
        </label>
        <label className={s.field}>Lý do bỏ campaign
          <input value={abandonReason} onChange={(event) => setAbandonReason(event.target.value)} />
        </label>
        <button className={s.button} type="button" disabled={blocked || abandonId !== openCampaign.campaign_id || !abandonReason.trim()}
          onClick={abandon}>Abandon campaign</button>
      </Panel> : null}
    </>
  )
}
