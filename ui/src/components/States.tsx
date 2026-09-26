import s from '../ui.module.css'

/** Explicit loading text next to the skeleton, so a screen reader is told too. */
export function Loading({ text = 'Đang đọc ledger…' }: { text?: string }) {
  return (
    <div className={s.skeleton} role="status" aria-live="polite">
      <span className={s.muted}>{text}</span>
      <i />
      <i style={{ width: '70%' }} />
      <i style={{ width: '85%' }} />
    </div>
  )
}

/** Empty says *why* it is empty: no data at all, or nothing matched the filter. */
export function Empty({ text }: { text: string }) {
  return <div className={s.empty}>{text}</div>
}

export function ErrorBox({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className={s.error} role="alert">
      <div>Không thể đọc dữ liệu: {text}</div>
      {onRetry ? (
        <button className={s.button} onClick={onRetry}>
          Thử lại
        </button>
      ) : null}
    </div>
  )
}
