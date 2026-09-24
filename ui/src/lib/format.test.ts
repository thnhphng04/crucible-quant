import { describe, expect, it } from 'vitest'
import { MISSING, arm, dateTime, num, percent, shortHash } from './format'

describe('format', () => {
  it('reads missing data as "Chưa có" and never as 0', () => {
    expect(num(null)).toBe(MISSING)
    expect(num(undefined)).toBe(MISSING)
    expect(num(NaN)).toBe(MISSING)
    expect(num(0)).toBe('0')
  })

  it('formats numbers with a Vietnamese decimal comma', () => {
    expect(num(1.2345, 3)).toBe('1,235')
    expect(num(1234.5, 1)).toBe('1.234,5')
  })

  it('never prints a percent sign on missing data', () => {
    expect(percent(null)).toBe(MISSING)
    expect(percent(12.34)).toBe('12,3%')
  })

  it('falls back to the raw string when a timestamp cannot be parsed', () => {
    expect(dateTime(null)).toBe(MISSING)
    expect(dateTime('không phải ngày')).toBe('không phải ngày')
  })

  it('shortens hashes and labels arms consistently', () => {
    expect(shortHash('abcdef0123456789')).toBe('abcdef012345')
    expect(shortHash(null)).toBe(MISSING)
    expect(arm('gp', 0)).toBe('gp-s0')
  })
})
