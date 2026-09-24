import s from '../ui.module.css'
import { dateTime } from '../lib/format'

/**
 * Every screen is timestamped with the snapshot it is showing — the page may be
 * older than the ledger, and research can still be writing behind it.
 */
export function Header({
  title,
  snapshotAt,
  loading,
  onRefresh,
}: {
  title: string
  snapshotAt?: string
  loading?: boolean
  onRefresh: () => void
}) {
  return (
    <header className={s.top}>
      <div>
        <h1>{title}</h1>
        <div className={s.muted}>
          Snapshot: {loading && !snapshotAt ? 'đang tải…' : dateTime(snapshotAt)}
        </div>
      </div>
      <button className={s.button} onClick={onRefresh}>
        ↻ Làm mới
      </button>
    </header>
  )
}
