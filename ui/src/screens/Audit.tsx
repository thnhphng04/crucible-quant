import { useParams, useSearchParams } from 'react-router-dom'
import s from '../ui.module.css'
import { useApi } from '../lib/api'
import { arm, dateTime, shortHash } from '../lib/format'
import type { EventsData, LockData } from '../lib/types'
import { Badge } from '../components/Badge'
import { JsonBlock } from '../components/CodeBlock'
import { Header } from '../components/Header'
import { Pagination } from '../components/Pagination'
import { Panel, Row } from '../components/Panel'
import { Empty, ErrorBox, Loading } from '../components/States'

const SIZE = 50

function lockKind(status: LockData['status']) {
  if (status === 'verified') return 'pass' as const
  if (status === 'mismatch') return 'reject' as const
  return 'warn' as const
}

function lockText(status: LockData['status']): string {
  if (status === 'verified') return 'Khớp hash trong ledger'
  if (status === 'mismatch') return 'Lệch hash — file lock đã bị sửa sau khi mở campaign'
  return 'Không tìm thấy file lock của campaign này'
}

export function Audit() {
  const { cid } = useParams()
  const [params, setParams] = useSearchParams()
  const page = Math.max(Number(params.get('page') ?? '1') || 1, 1)
  const event = params.get('event') ?? ''

  const query = new URLSearchParams({ page: String(page), size: String(SIZE) })
  if (event) query.set('event', event)
  const events = useApi<EventsData>(`/api/campaigns/${cid}/events?${query.toString()}`)
  const lock = useApi<LockData>(`/api/campaigns/${cid}/lock`, false)

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.set('page', '1')
    setParams(next)
  }

  if (events.error && !events.data) return <ErrorBox text={events.error} onRetry={events.reload} />

  return (
    <>
      <Header
        title="Audit & cấu hình khóa"
        snapshotAt={events.data?.snapshot_at}
        loading={events.loading}
        onRefresh={events.reload}
      />
      {events.error ? <ErrorBox text={events.error} onRetry={events.reload} /> : null}
      <div className={s.split}>
        <Panel title="Evaluation lock">
          {lock.error && !lock.data ? (
            <ErrorBox text={lock.error} onRetry={lock.reload} />
          ) : !lock.data ? (
            <Loading text="Đang đọc lock…" />
          ) : (
            <>
              <p>
                <Badge kind={lockKind(lock.data.status)}>{lock.data.status}</Badge>{' '}
                {lockText(lock.data.status)}
              </p>
              <p className={s.muted}>
                Hash trong ledger: <code>{shortHash(lock.data.expected_hash, 16)}</code>
                {lock.data.actual_hash ? (
                  <>
                    {' '}
                    · hash file: <code>{shortHash(lock.data.actual_hash, 16)}</code>
                  </>
                ) : null}
              </p>
              {lock.data.lock ? (
                <JsonBlock value={lock.data.lock} />
              ) : (
                <Empty text="Không có nội dung lock để hiển thị." />
              )}
            </>
          )}
        </Panel>
        <Panel title="Audit timeline">
          <div className={s.filters}>
            <select
              value={event}
              onChange={(e) => update('event', e.target.value)}
              aria-label="Lọc theo loại event"
            >
              <option value="">Mọi event</option>
              {(events.data?.names ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          {!events.data ? (
            <Loading text="Đang đọc audit log…" />
          ) : events.data.items.length === 0 ? (
            <Empty
              text={event ? `Không có event “${event}” trong campaign này.` : 'Chưa có audit event.'}
            />
          ) : (
            <>
              {events.data.items.map((row) => (
                <Row
                  key={row.id}
                  label={dateTime(row.ts)}
                  state={<Badge kind={row.event.includes('WARNING') ? 'warn' : 'neutral'}>{row.event}</Badge>}
                >
                  {arm(row.engine, row.seed)}
                  {row.agent ? <span className={s.muted}> · {row.agent}</span> : null}
                  {row.strategy_hash ? (
                    <span className={s.muted}> · {shortHash(row.strategy_hash, 10)}</span>
                  ) : null}
                </Row>
              ))}
              <Pagination
                page={events.data.page}
                size={events.data.size}
                total={events.data.total}
                onPage={(value) => update('page', String(value))}
              />
            </>
          )}
        </Panel>
      </div>
    </>
  )
}
