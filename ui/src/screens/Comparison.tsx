import { Link, useParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { arm, num, percent } from '../lib/format'
import type { CheckpointRow, ComparisonData } from '../lib/types'
import { ArmBadge, Badge } from '../components/Badge'
import { Header } from '../components/Header'
import { LineChart } from '../components/LineChart'
import { Panel, TableWrap } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

const GP = '#58a6ff'
const RANDOM = '#b392f0'

function checkpointsOf(rows: CheckpointRow[], engine: string): CheckpointRow[] {
  return rows.filter((row) => row.engine === engine)
}

export function Comparison() {
  const { cid } = useParams()
  const { data, error, loading, reload } = useApi<ComparisonData>(
    `/api/campaigns/${cid}/comparison`,
  )

  if (error && !data) return <ErrorBox text={error} onRetry={reload} />
  if (!data) return <Loading text="Đang đọc so sánh…" />

  const gp = checkpointsOf(data.checkpoints, 'gp')
  const random = checkpointsOf(data.checkpoints, 'random')
  // Each arm reaches its n-th checkpoint at its own trial count, so the shared axis is
  // the checkpoint ordinal. The exact trial counts stay in the table below.
  const width = Math.max(gp.length, random.length)
  const labels = Array.from({ length: width }, (_, i) => `#${i + 1}`)

  return (
    <>
      <Header
        title="GP ↔ Random"
        snapshotAt={data.snapshot_at}
        loading={loading}
        onRefresh={reload}
      />
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      {data.warnings.length > 0 ? (
        <Panel
          title="Cảnh báo IS→OOS đã phát sinh"
          note="Cảnh báo được giữ trong audit và không tự quyết định arm nào thắng."
        >
          <div className={s.badgeRow}>
            {data.warnings.map((one) => (
              <Badge key={arm(one.engine, one.seed)} kind="warn">
                ⚠ {arm(one.engine, one.seed)}
              </Badge>
            ))}
          </div>
        </Panel>
      ) : null}
      <Panel
        title="Ngân sách và tỷ lệ qua ④"
        note="“Tỷ lệ qua ④” là tỷ lệ ứng viên vượt gate PBO, không phải hiệu quả đầu tư."
      >
        {data.units.length ? (
          <TableWrap>
            <table>
              <caption>
                Mỗi arm là một bộ (instrument, direction, engine, seed) với quota trial riêng.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Arm</th>
                  <th scope="col">Trial / quota</th>
                  <th scope="col">Tỷ lệ qua ④</th>
                  <th scope="col">Đã qua ④</th>
                </tr>
              </thead>
              <tbody>
                {data.units.map((unit) => (
                  <tr key={unit.unit}>
                    <td>
                      <ArmBadge engine={unit.engine} seed={unit.seed} />
                      <div className="scope">{unit.instrument}-{unit.direction}</div>
                    </td>
                    <td>
                      <div>
                        {num(unit.trials)} / {unit.quota ? num(unit.quota) : 'Chưa có'}
                      </div>
                      <div
                        className={s.bar}
                        role="img"
                        aria-label={`Đã dùng ${unit.trials} trong ${unit.quota || 0} trial`}
                      >
                        <i
                          style={{
                            width: unit.quota
                              ? `${Math.min(100, (unit.trials / unit.quota) * 100)}%`
                              : '0%',
                          }}
                        />
                      </div>
                    </td>
                    <td>{percent(unit.pass_rate)}</td>
                    <td>{num(unit.passed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : (
          <Empty text="Chưa có arm nào ghi trial." />
        )}
      </Panel>
      <Panel title="Checkpoint IS → OOS">
        <LineChart
          ariaLabel="Sharpe IS và OOS median theo checkpoint của GP và random"
          caption="Trục ngang là checkpoint thứ mấy của từng arm (số trial tương ứng xem bảng dưới); trục dọc là Sharpe. Đường ngắt là checkpoint thiếu số liệu."
          labels={labels}
          digits={2}
          emptyText="Chưa có checkpoint IS→OOS"
          series={[
            {
              name: 'GP · IS',
              color: GP,
              values: gp.map((row) => row.detail.is_sharpe),
            },
            {
              name: 'GP · OOS',
              color: '#2f6ea8',
              values: gp.map((row) => row.detail.oos_median),
            },
            {
              name: 'Random · IS',
              color: RANDOM,
              values: random.map((row) => row.detail.is_sharpe),
            },
            {
              name: 'Random · OOS',
              color: '#6b52a3',
              values: random.map((row) => row.detail.oos_median),
            },
          ]}
        />
        {data.checkpoints.length ? (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th scope="col">Arm</th>
                  <th scope="col">Trial</th>
                  <th scope="col">IS Sharpe</th>
                  <th scope="col">OOS median</th>
                  <th scope="col">Ứng viên</th>
                </tr>
              </thead>
              <tbody>
                {data.checkpoints.map((row, index) => (
                  <tr key={`${arm(row.engine, row.seed)}-${row.ts}-${index}`}>
                    <td>{arm(row.engine, row.seed)}</td>
                    <td>{num(row.detail.trials)}</td>
                    <td>{num(row.detail.is_sharpe, 3)}</td>
                    <td>{num(row.detail.oos_median, 3)}</td>
                    <td>
                      {row.detail.candidate_id ? (
                        <Link
                          className={s.link}
                          to={`/campaign/${cid}/candidate/${encodeURIComponent(row.detail.candidate_id)}`}
                        >
                          {row.detail.candidate_id}
                        </Link>
                      ) : (
                        'Chưa có'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        ) : null}
      </Panel>
    </>
  )
}
