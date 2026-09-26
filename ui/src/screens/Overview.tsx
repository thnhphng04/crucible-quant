import { useParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { dateTime, shortHash } from '../lib/format'
import type { OverviewData } from '../lib/types'
import { Header } from '../components/Header'
import { Metric, MetricGrid, Panel } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

/** A verdict is never styled as a win or a loss — only research state is coloured. */
function verdictTone(outcome: string): string {
  if (outcome === 'complete') return s.verdictComplete
  if (outcome === 'research') return s.verdictResearch
  return ''
}

export function Overview() {
  const { cid } = useParams()
  const { data, error, loading, reload } = useApi<OverviewData>(`/api/campaigns/${cid}/overview`)

  if (error && !data) return <ErrorBox text={error} onRetry={reload} />
  if (!data) return <Loading text="Đang đọc campaign…" />

  const protocolVersion = data.protocol?.protocol?.version
  return (
    <>
      <Header title={cid ?? ''} snapshotAt={data.snapshot_at} loading={loading} onRefresh={reload} />
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      <section className={`${s.panel} ${s.verdict} ${verdictTone(data.verdict.outcome)}`}>
        <h2>{data.verdict.label}</h2>
        <div>{data.verdict.reason}</div>
      </section>
      <MetricGrid>
        <Metric label="Trạng thái campaign" value={data.campaign.status} raw />
        <Metric label="Loại campaign" value={data.campaign.purpose} raw />
        <Metric label="Trial trong campaign" value={data.counts.trials} />
        <Metric label="Cảnh báo đã phát sinh" value={data.counts.warnings} />
        <Metric label="Ứng viên đã đo" value={data.counts.candidates} />
        <Metric label="Trial verdict PASS" value={data.counts.passed} />
        <Metric label="Audit event" value={data.counts.events} />
        <Metric label="Hoạt động cuối" value={dateTime(data.last_activity)} raw />
      </MetricGrid>
      <Panel
        title="Bối cảnh"
        note="OPEN chỉ là trạng thái campaign trong ledger; giao diện không suy đoán là có process đang chạy."
      >
        <MetricGrid>
          <Metric label="Mở lúc" value={dateTime(data.campaign.started_at)} raw />
          <Metric label="Ngân sách trial" value={data.campaign.trial_budget} />
          <Metric label="Khoảng holdout (chưa mở)" value={data.campaign.holdout_range} raw />
          <Metric
            label="Protocol so sánh"
            value={protocolVersion ? `v${protocolVersion}` : 'Chưa khóa'}
            raw
          />
        </MetricGrid>
        <p className={s.muted}>
          Lock hash: <code>{shortHash(data.campaign.lock_hash, 16)}</code>
        </p>
        {data.campaign.abandoned_at ? (
          <p>
            Bỏ campaign lúc {dateTime(data.campaign.abandoned_at)} — lý do ghi trong ledger:{' '}
            {data.campaign.abandonment_reason || 'Chưa có'}
          </p>
        ) : null}
      </Panel>
      {data.counts.trials === 0 ? <Empty text="Campaign chưa có trial nào." /> : null}
    </>
  )
}
