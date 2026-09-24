/** Vietnamese formatting helpers. Missing data is always "Chưa có", never 0. */

export const MISSING = 'Chưa có'

export function isMissing(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === 'number' && !isFinite(value))
}

export function num(value: unknown, digits = 2): string {
  if (isMissing(value)) return MISSING
  if (typeof value === 'number') {
    return value.toLocaleString('vi-VN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    })
  }
  return String(value)
}

export function percent(value: unknown, digits = 1): string {
  return isMissing(value) ? MISSING : `${num(value, digits)}%`
}

export function dateTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return MISSING
  const parsed = new Date(value)
  return isNaN(parsed.getTime()) ? value : parsed.toLocaleString('vi-VN')
}

export function shortHash(value: unknown, length = 12): string {
  return typeof value === 'string' && value ? value.slice(0, length) : MISSING
}

/** `gp-s0`: the arm label used everywhere so the same unit always reads the same. */
export function arm(engine: string, seed: number | string): string {
  return `${engine}-s${seed}`
}
