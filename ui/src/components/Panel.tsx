import type { ReactNode } from 'react'
import s from '../ui.module.css'
import { num } from '../lib/format'

export function Panel({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: ReactNode
}) {
  return (
    <section className={s.panel}>
      <h2>{title}</h2>
      {note ? <p className={s.muted}>{note}</p> : null}
      {children}
    </section>
  )
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className={s.grid}>{children}</div>
}

/** One number with its label. `raw` skips number formatting for ids and hashes. */
export function Metric({
  label,
  value,
  digits = 2,
  raw = false,
}: {
  label: string
  value: unknown
  digits?: number
  raw?: boolean
}) {
  return (
    <div className={s.card}>
      <span>{label}</span>
      <strong>{raw ? (value == null || value === '' ? 'Chưa có' : String(value)) : num(value, digits)}</strong>
    </div>
  )
}

/** A timeline line: identifier, state, then the explanation. */
export function Row({
  label,
  state,
  children,
}: {
  label: ReactNode
  state: ReactNode
  children: ReactNode
}) {
  return (
    <div className={s.row}>
      <code>{label}</code>
      <span>{state}</span>
      <span>{children}</span>
    </div>
  )
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className={s.tableWrap}>{children}</div>
}
