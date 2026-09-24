import { Link, useParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { dateTime, num, percent, shortHash } from '../lib/format'
import type { CandidateDetail, Curve, GateRow, Measurement } from '../lib/types'
import { Badge } from '../components/Badge'
import { JsonBlock } from '../components/CodeBlock'
import { Header } from '../components/Header'
import { LineChart } from '../components/LineChart'
import { Metric, MetricGrid, Panel, Row } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

function gateState(gate: GateRow) {
  if (gate.passed) return <Badge kind="pass">✓ PASS</Badge>
  const errored = Boolean(gate.detail && 'error' in gate.detail)
  return <Badge kind="reject">{errored ? '! ERROR' : '✕ REJECT'}</Badge>
}

function curveNote(curve: Curve): string {
  if (curve.status === 'missing') return 'Không tìm thấy artifact returns của trial này.'
  if (curve.status === 'error') return `Không đọc được artifact returns (${curve.message}).`
  return ''
}

function maxDrawdown(curve: Curve): number | null {
  if (curve.status !== 'ok' || !curve.points.length) return null
  return Math.min(...curve.points.map((point) => point.drawdown)) * 100
}

function EquityPanel({ measurement }: { measurement: Measurement }) {
  const curve = measurement.curve
  const note = curveNote(curve)
  return (
    <div>
      <p>
        <Badge>{measurement.source}</Badge> Sharpe IS <b>{num(measurement.sharpe_is, 3)}</b> ·{' '}
        {measurement.universe} {measurement.timeframe} · {measurement.timerange}
      </p>
      {note ? (
        <Empty text={note} />
      ) : (
        <LineChart
          height={220}
          digits={3}
          ariaLabel={`Đường tăng trưởng vốn chuẩn hóa của lần đo ${measurement.id}`}
          caption={`Trục dọc là vốn chuẩn hóa về 1. Drawdown sâu nhất: ${percent(maxDrawdown(curve))}`}
          labels={curve.points.map((point) => point.ts.slice(0, 10))}
          series={[{ name: 'Equity', color: '#45d483', values: curve.points.map((p) => p.equity) }]}
        />
      )}
    </div>
  )
}

export function Candidate() {
  const { cid, candidateId } = useParams()
  // A candidate record never changes once written, so this screen does not poll.
  const { data, error, loading, reload } = useApi<CandidateDetail>(
    `/api/campaigns/${cid}/candidates/${encodeURIComponent(candidateId ?? '')}`,
    false,
  )

  if (error && !data) return <ErrorBox text={error} onRetry={reload} />
  if (!data) return <Loading text="Đang đọc ứng viên…" />

  const first = data.measurements[0]
  return (
    <>
      <Header
        title={candidateId ?? ''}
        snapshotAt={data.snapshot_at}
        loading={loading}
        onRefresh={reload}
      />
      <p className={s.muted}>
        <Link className={s.link} to={`/campaign/${cid}/candidates`}>
          ← Về danh sách ứng viên
        </Link>
      </p>
      <MetricGrid>
        <Metric label="Strategy hash" value={shortHash(data.strategy_hash)} raw />
        <Metric label="Số lần đo" value={data.measurements.length} />
        <Metric label="Số gate result" value={data.gates.length} />
        <Metric
          label="Source artifact"
          value={data.source.status === 'ok' ? 'Đã lưu' : 'Thiếu'}
          raw
        />
      </MetricGrid>
      {first ? (
        <MetricGrid>
          <Metric label="Arm" value={`${first.engine}-s${first.seed}`} raw />
          <Metric label="Island" value={first.island} />
          <Metric label="Ô feature map" value={first.cell_id} raw />
          <Metric label="Ghi lúc" value={dateTime(first.ts)} raw />
        </MetricGrid>
      ) : null}
      <Panel
        title="Gate theo thứ tự"
        note="Gate được đọc theo đúng thứ tự ledger ghi; gate đầu tiên thất bại là gate đã chặn ứng viên."
      >
        {data.gates.length ? (
          data.gates.map((gate) => (
            <Row key={gate.id} label={gate.gate} state={gateState(gate)}>
              {gate.reason || 'Chưa có lý do ghi kèm'}
              {gate.value !== null ? <span className={s.muted}> · value {num(gate.value, 4)}</span> : null}
            </Row>
          ))
        ) : (
          <Empty text="Ứng viên chưa qua gate nào." />
        )}
      </Panel>
      <div className={s.split}>
        <Panel title="Các lần đo">
          {data.measurements.length ? (
            data.measurements.map((measurement) => (
              <EquityPanel key={measurement.id} measurement={measurement} />
            ))
          ) : (
            <Empty text="Ứng viên đã submit nhưng chưa có trial nào." />
          )}
        </Panel>
        <Panel title="Nguồn gốc và tham số">
          {data.measurements[0] ? (
            <>
              <h3 className={s.muted} style={{ fontSize: '0.8rem' }}>
                Tham số của lần đo đầu
              </h3>
              <JsonBlock value={data.measurements[0].params} />
            </>
          ) : null}
          <h3 className={s.muted} style={{ fontSize: '0.8rem' }}>
            Event submit
          </h3>
          {data.submission ? (
            <>
              <p>
                <Badge>{data.submission.event}</Badge> {dateTime(data.submission.ts)}
              </p>
              <JsonBlock value={data.submission.detail} />
            </>
          ) : (
            <Empty text="Không có event submit tương ứng." />
          )}
        </Panel>
      </div>
      <Panel
        title="Code đã lưu theo hash"
        note="Đây là artifact đúng bằng hash đã ghi trong ledger, không phải code sinh lại."
      >
        {data.source.status === 'ok' && data.source.text ? (
          <pre className={s.code}>{data.source.text}</pre>
        ) : (
          <Empty text="Artifact source bị thiếu hoặc nằm ngoài results/strategies." />
        )}
      </Panel>
    </>
  )
}
