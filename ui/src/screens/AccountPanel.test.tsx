import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { AccountPanel } from './AccountPanel'
import { mockApi } from '../test/mockApi'

afterEach(() => vi.unstubAllGlobals())

const ROUTE = '/api/campaigns/c1/account/' + 'a'.repeat(64)

function payload(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    portfolio_hash: 'a'.repeat(64),
    initial_cash: 100000,
    points: [
      { ts: '2026-01-01', equity: 100000, residual: 0 },
      { ts: '2026-01-02', equity: 100060, residual: 0 },
    ],
    slots: [
      { slot: 'BTC/USDT:USDT-long', final: 100, values: [0, 100] },
      { slot: 'ETH/USDT:USDT-short', final: -40, values: [0, -40] },
    ],
    max_residual: 0,
    reconciles: true,
    snapshot_at: '2026-09-26T00:00:00+00:00',
    ...overrides,
  }
}

test('every slot is a table row, not a chart line', async () => {
  // Ten slots would overlap `LineChart`'s fixed-pitch legend into nonsense, so the decomposition
  // is a table and the account's own equity is the single line.
  mockApi({ [ROUTE]: payload() })
  render(<AccountPanel cid="c1" portfolioHash={'a'.repeat(64)} />)
  expect(await screen.findByText('BTC/USDT:USDT-long')).toBeTruthy()
  expect(screen.getByText('ETH/USDT:USDT-short')).toBeTruthy()
})

test('the reconciliation is shown as a measured number, not a claim', async () => {
  mockApi({ [ROUTE]: payload() })
  render(<AccountPanel cid="c1" portfolioHash={'a'.repeat(64)} />)
  expect(await screen.findByText('Khớp')).toBeTruthy()
  expect(screen.getByText(/sai số lớn nhất/)).toBeTruthy()
})

test('a drift is reported rather than hidden', async () => {
  mockApi({ [ROUTE]: payload({ reconciles: false, max_residual: 12.5 }) })
  render(<AccountPanel cid="c1" portfolioHash={'a'.repeat(64)} />)
  expect(await screen.findByText('Lệch')).toBeTruthy()
})

test('an absent curve reads as Chưa có, never as zero', async () => {
  // A flat line at zero would be a claim about the account; absence is not a value.
  mockApi({
    [ROUTE]: {
      status: 'missing',
      portfolio_hash: 'a'.repeat(64),
      initial_cash: null,
      points: [],
      slots: [],
      max_residual: null,
      reconciles: null,
      snapshot_at: '2026-09-26T00:00:00+00:00',
    },
  })
  render(<AccountPanel cid="c1" portfolioHash={'a'.repeat(64)} />)
  expect(await screen.findByText(/Chưa có đường equity/)).toBeTruthy()
  expect(screen.queryByText('Khớp')).toBeNull()
})
