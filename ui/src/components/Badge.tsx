import type { ReactNode } from 'react'
import s from '../ui.module.css'

export type BadgeKind = 'pass' | 'reject' | 'warn' | 'gp' | 'random' | 'neutral'

/** Status always carries its own text: colour alone never encodes the state. */
export function Badge({
  children,
  kind = 'neutral',
}: {
  children: ReactNode
  kind?: BadgeKind
}) {
  const tone = kind === 'neutral' ? '' : (s[kind] ?? '')
  return <span className={`${s.badge} ${tone}`.trim()}>{children}</span>
}

/** A trial verdict, where PRE_TRIAL means "submitted, not measured yet". */
export function VerdictBadge({ verdict }: { verdict: string }) {
  if (verdict === 'PASS') return <Badge kind="pass">✓ PASS</Badge>
  if (verdict === 'PRE_TRIAL') return <Badge kind="warn">· Chưa đo</Badge>
  return <Badge kind="reject">✕ {verdict}</Badge>
}

/** GP is blue, random is violet — the same two colours as the comparison chart. */
export function ArmBadge({ engine, seed }: { engine: string; seed: number | string }) {
  const kind: BadgeKind = engine === 'gp' ? 'gp' : engine === 'random' ? 'random' : 'neutral'
  return (
    <Badge kind={kind}>
      {engine}-s{seed}
    </Badge>
  )
}
