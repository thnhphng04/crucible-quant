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

/**
 * `BTCUSDT-long-gp-s0`: one unit of search, spelled the way the API and the CLI spell it.
 *
 * A campaign from before P3-12 stores no scope, and the API fills in `legacy_spot`/`long` rather
 * than leaving it blank, so an old report reads as a report and not as missing data.
 */
export function unit(
  instrument: string,
  direction: string,
  engine: string,
  seed: number | string,
): string {
  return `${instrument}-${direction}-${arm(engine, seed)}`
}
