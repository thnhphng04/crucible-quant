import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi, useDebounced } from '../lib/api'
import { dateTime, num } from '../lib/format'
import type { CandidateRow, PageData } from '../lib/types'
import { ArmBadge, VerdictBadge } from '../components/Badge'
import { Header } from '../components/Header'
import { Pagination } from '../components/Pagination'
import { Panel, TableWrap } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

const SIZE = 50

export function Candidates() {
  const { cid } = useParams()
  const [params, setParams] = useSearchParams()
  const [text, setText] = useState(params.get('q') ?? '')
  const debounced = useDebounced(text)

  // The typed query only enters the URL once typing pauses, so polling stays cheap.
  useEffect(() => {
    const next = new URLSearchParams(params)
    if ((next.get('q') ?? '') === debounced) return
    if (debounced) next.set('q', debounced)
    else next.delete('q')
    next.set('page', '1')
    setParams(next, { replace: true })
    // `params` is intentionally read fresh each time the debounced value settles.
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const page = Math.max(Number(params.get('page') ?? '1') || 1, 1)
  const query = new URLSearchParams({ page: String(page), size: String(SIZE) })
  for (const key of ['q', 'engine', 'seed', 'island'] as const) {
    const value = params.get(key)
    if (value) query.set(key, value)
  }
  const { data, error, loading, reload } = useApi<PageData<CandidateRow>>(
    `/api/campaigns/${cid}/candidates?${query.toString()}`,
  )

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('page', '1')
    setParams(next)
  }
  const setPage = (value: number) => {
    const next = new URLSearchParams(params)
    next.set('page', String(value))
    setParams(next)
  }

  const filtered = Boolean(params.get('q') || params.get('engine') || params.get('seed') || params.get('island'))

  return (
    <>
      <Header
        title="Ứng viên & trial"
        snapshotAt={data?.snapshot_at}
        loading={loading}
        onRefresh={reload}
      />
      {error ? <ErrorBox text={error} onRetry={reload} /> : null}
      <Panel title="Bộ lọc">
        <div className={s.filters}>
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Candidate ID hoặc strategy hash"
            aria-label="Tìm theo candidate ID hoặc strategy hash"
            size={34}
          />
          <select
            value={params.get('engine') ?? ''}
            onChange={(event) => setFilter('engine', event.target.value)}
            aria-label="Lọc theo engine"
          >
            <option value="">Mọi engine</option>
            <option value="gp">gp</option>
            <option value="random">random</option>
            <option value="manual">manual</option>
          </select>
          <input
            value={params.get('seed') ?? ''}
            onChange={(event) => setFilter('seed', event.target.value.replace(/\D/g, ''))}
            placeholder="Seed"
            aria-label="Lọc theo seed"
            inputMode="numeric"
            size={6}
          />
          <input
            value={params.get('island') ?? ''}
            onChange={(event) => setFilter('island', event.target.value)}
            placeholder="Island"
            aria-label="Lọc theo island"
            size={8}
          />
        </div>
        {!data ? (
          <Loading text="Đang đọc danh sách ứng viên…" />
        ) : data.items.length === 0 ? (
          <Empty
            text={
              filtered
                ? 'Không có ứng viên nào khớp bộ lọc hiện tại.'
                : 'Campaign chưa ghi ứng viên nào.'
            }
          />
        ) : (
          <>
            <TableWrap>
              <table>
                <caption>
                  “Chưa đo” là ứng viên đã submit nhưng chưa có trial — không phải bị loại.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Ứng viên</th>
                    <th scope="col">Arm</th>
                    <th scope="col">Island</th>
                    <th scope="col">Sharpe IS</th>
                    <th scope="col">Gate chặn</th>
                    <th scope="col">Verdict</th>
                    <th scope="col">Thời điểm</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={`${row.candidate_id}-${row.trial_id ?? 'pre'}`}>
                      <td>
                        <Link
                          className={s.link}
                          to={`/campaign/${cid}/candidate/${encodeURIComponent(row.candidate_id)}`}
                        >
                          {row.candidate_id}
                        </Link>
                      </td>
                      <td>
                        <ArmBadge engine={row.engine} seed={row.seed} />
                      </td>
                      <td>{num(row.island)}</td>
                      <td>{num(row.sharpe_is, 3)}</td>
                      <td>{row.gate_failed ? <code>{row.gate_failed}</code> : '—'}</td>
                      <td>
                        <VerdictBadge verdict={row.verdict} />
                      </td>
                      <td>{dateTime(row.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={data.page} size={data.size} total={data.total} onPage={setPage} />
          </>
        )}
      </Panel>
    </>
  )
}
