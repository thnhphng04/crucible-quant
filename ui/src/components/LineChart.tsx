import s from '../ui.module.css'
import { num } from '../lib/format'
import { Empty } from './States'

export interface Series {
  name: string
  color: string
  /** Gaps are allowed: a missing point breaks the line instead of reading as 0. */
  values: (number | null | undefined)[]
}

const W = 640
const PAD = { left: 62, right: 18, top: 28, bottom: 36 }

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && isFinite(value)
}

/** Contiguous runs of drawable points, so a gap never becomes a straight line. */
function segments(values: (number | null | undefined)[]): { i: number; v: number }[][] {
  const runs: { i: number; v: number }[][] = []
  let run: { i: number; v: number }[] = []
  values.forEach((value, i) => {
    if (finite(value)) {
      run.push({ i, v: value })
    } else if (run.length) {
      runs.push(run)
      run = []
    }
  })
  if (run.length) runs.push(run)
  return runs
}

/**
 * A small line chart with real axes and ticks — the numbers stay readable when the
 * page is zoomed, because the SVG scales with its box instead of being pixel-sized.
 */
export function LineChart({
  series,
  labels,
  ariaLabel,
  caption,
  height = 240,
  digits = 2,
  emptyText = 'Chưa có dữ liệu để vẽ',
}: {
  series: Series[]
  labels: string[]
  ariaLabel: string
  caption?: string
  height?: number
  digits?: number
  emptyText?: string
}) {
  const all = series.flatMap((one) => one.values).filter(finite)
  if (!all.length) return <Empty text={emptyText} />

  const count = Math.max(labels.length, ...series.map((one) => one.values.length))
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  if (hi - lo < 1e-9) {
    const pad = Math.max(Math.abs(hi) * 0.1, 0.5)
    lo -= pad
    hi += pad
  }

  const plotW = W - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (count > 1 ? (i * plotW) / (count - 1) : plotW / 2)
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH

  const yTicks = [0, 1, 2, 3].map((k) => lo + ((hi - lo) * k) / 3)
  const xTickIndexes =
    count <= 1 ? [0] : count <= 3 ? labels.map((_, i) => i) : [0, Math.floor((count - 1) / 2), count - 1]

  return (
    <figure style={{ margin: 0 }}>
      <svg
        className={s.chart}
        viewBox={`0 0 ${W} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={ariaLabel}
      >
        <title>{ariaLabel}</title>
        {yTicks.map((tick) => (
          <g key={tick}>
            <line className={s.gridline} x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} />
            <text className={s.tick} x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
              {num(tick, digits)}
            </text>
          </g>
        ))}
        <line className={s.axis} x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={PAD.top + plotH} />
        <line
          className={s.axis}
          x1={PAD.left}
          x2={W - PAD.right}
          y1={PAD.top + plotH}
          y2={PAD.top + plotH}
        />
        {xTickIndexes.map((i) => (
          <text
            key={i}
            className={s.tick}
            x={x(i)}
            y={PAD.top + plotH + 20}
            textAnchor={i === 0 ? 'start' : i === count - 1 ? 'end' : 'middle'}
          >
            {labels[i] ?? i + 1}
          </text>
        ))}
        {series.map((one) =>
          segments(one.values).map((run, index) =>
            run.length === 1 ? (
              <circle
                key={`${one.name}-${index}`}
                cx={x(run[0].i)}
                cy={y(run[0].v)}
                r={3}
                fill={one.color}
              />
            ) : (
              <polyline
                key={`${one.name}-${index}`}
                fill="none"
                stroke={one.color}
                strokeWidth={2.5}
                points={run.map((point) => `${x(point.i)},${y(point.v)}`).join(' ')}
              />
            ),
          ),
        )}
        {series.map((one, index) => (
          <g key={one.name} transform={`translate(${PAD.left + index * 130}, 16)`}>
            <line x1={0} x2={18} y1={0} y2={0} stroke={one.color} strokeWidth={3} />
            <text className={`${s.tick} ${s.legend}`} x={24} y={4} fill={one.color}>
              {one.name}
            </text>
          </g>
        ))}
      </svg>
      {caption ? (
        <figcaption className={s.muted} style={{ fontSize: '0.78rem', marginTop: 6 }}>
          {caption}
        </figcaption>
      ) : null}
    </figure>
  )
}
