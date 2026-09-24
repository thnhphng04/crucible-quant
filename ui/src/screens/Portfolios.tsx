import { useParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { dateTime, num, shortHash } from '../lib/format'
import type { PortfolioData } from '../lib/types'
import { Badge } from '../components/Badge'
import { Header } from '../components/Header'
import { Metric, MetricGrid, Panel, Row, TableWrap } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

export function Portfolios() {
  const { cid } = useParams()
  const { data, error, loading, reload } = useApi<PortfolioData>(`/api/campaigns/${cid}/portfolios`)

  if (error && !data) return <ErrorBox text={error} onRetry={reload} />
  if (!data) return <Loading text="Đang đọc danh mục…" />

  return (
    <>
      <Header
        title="Danh mục & calibration"
        snapshotAt={data.snapshot_at}
        loading={loading}
        onRefresh={reload}
      />
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      <MetricGrid>
        <Metric label="Portfolio variant" value={data.items.length} />
        <Metric label="Trial trong campaign" value={data.ledger_trials} />
        <Metric label="Lần calibration" value={data.calibrations.length} />
        <Metric
          label="Variant còn hiện hành"
          value={data.items.filter((item) => !item.stale).length}
        />
      </MetricGrid>
      <Panel
        title="Portfolio variants"
        note="“Đã cũ” nghĩa là variant được dựng trên một snapshot trial khác với hiện tại, không phải là variant sai."
      >
        {data.items.length ? (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th scope="col">Hash</th>
                  <th scope="col">Thời điểm</th>
                  <th scope="col">Thành viên</th>
                  <th scope="col">Sharpe IS</th>
                  <th scope="col">Độ mới</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.portfolio_hash}>
                    <td className="mono">{shortHash(item.portfolio_hash)}</td>
                    <td>{dateTime(item.ts)}</td>
                    <td>{num(item.members.length)}</td>
                    <td>{num(item.sharpe_is, 3)}</td>
                    <td>
                      <Badge kind={item.stale ? 'warn' : 'pass'}>
                        {item.stale ? 'Đã cũ theo ledger' : 'Hiện hành'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : (
          <Empty text="Campaign chưa hợp nhất portfolio variant nào." />
        )}
      </Panel>
      <Panel
        title="Calibration timeline"
        note="Calibration chạy một lần trước khi freeze; mỗi lần đo vẫn là một dòng trials."
      >
        {data.calibrations.length ? (
          data.calibrations.map((run) => (
            <Row
              key={`${run.candidate_id}-${run.started_at}`}
              label={run.candidate_id}
              state={
                <Badge kind={run.outcome ? 'pass' : 'warn'}>
                  {run.outcome ?? 'Chưa kết thúc'}
                </Badge>
              }
            >
              {num(run.attempts ?? 0)}/{num(run.budget)} lần · {num(run.trials ?? 0)} trial ·{' '}
              {num(run.errors ?? 0)} lỗi
              <span className={s.muted}> · bắt đầu {dateTime(run.started_at)}</span>
            </Row>
          ))
        ) : (
          <Empty text="Chưa có calibration nào." />
        )}
      </Panel>
    </>
  )
}
