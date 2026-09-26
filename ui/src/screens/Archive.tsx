import { useParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { num } from '../lib/format'
import type { ArchiveData, ArchiveRow } from '../lib/types'
import { Header } from '../components/Header'
import { Metric, MetricGrid, Panel } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

interface CellSummary {
  id: string
  trials: number
  eligible: number
  bestSharpe: number | null
}

/** One row per feature-map cell — a projection of the map, not a 2-D coverage grid. */
function summarise(rows: ArchiveRow[]): CellSummary[] {
  const cells = new Map<string, CellSummary>()
  for (const row of rows) {
    const cell = cells.get(row.cell_id) ?? {
      id: row.cell_id,
      trials: 0,
      eligible: 0,
      bestSharpe: null,
    }
    cell.trials += 1
    cell.eligible += row.eligible ? 1 : 0
    if (typeof row.sharpe_is === 'number' && isFinite(row.sharpe_is)) {
      cell.bestSharpe = cell.bestSharpe === null ? row.sharpe_is : Math.max(cell.bestSharpe, row.sharpe_is)
    }
    cells.set(row.cell_id, cell)
  }
  return [...cells.values()].sort((a, b) => b.eligible - a.eligible || b.trials - a.trials)
}

export function Archive() {
  const { cid } = useParams()
  const { data, error, loading, reload } = useApi<ArchiveData>(`/api/campaigns/${cid}/archive`)

  if (error && !data) return <ErrorBox text={error} onRetry={reload} />
  if (!data) return <Loading text="Đang đọc archive…" />

  const cells = summarise(data.items)
  return (
    <>
      <Header
        title="Archive · phép chiếu ô"
        snapshotAt={data.snapshot_at}
        loading={loading}
        onRefresh={reload}
      />
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      <MetricGrid>
        <Metric label="Ô đã khám phá" value={data.search_cells} />
        <Metric label="Ô có ứng viên qua ④" value={data.eligible_cells} />
        <Metric label="Trial có cell_id" value={data.items.length} />
        <Metric
          label="Ứng viên qua ④"
          value={data.items.filter((row) => row.eligible).length}
        />
      </MetricGrid>
      <Panel
        title="Các ô feature map"
        note="Đây là danh sách/phép chiếu của map nhiều chiều, không phải tổng coverage hai chiều."
      >
        {cells.length ? (
          <div className={s.heat}>
            {cells.map((cell) => (
              <div className={s.cell} key={cell.id}>
                <code>{cell.id}</code>
                <div className={s.muted}>
                  {num(cell.trials)} trial · {num(cell.eligible)} qua ④
                </div>
                <div>Sharpe IS cao nhất: {num(cell.bestSharpe, 2)}</div>
              </div>
            ))}
          </div>
        ) : (
          <Empty text="Chưa có trial nào ghi cell_id — archive chỉ có dữ liệu khi engine GP chạy với feature map." />
        )}
      </Panel>
    </>
  )
}
