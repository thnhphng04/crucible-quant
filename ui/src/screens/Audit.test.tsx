import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Audit } from './Audit'
import { mockApi } from '../test/mockApi'

function event(id: number, name: string) {
  return {
    id,
    ts: '2026-09-24T00:15:59+00:00',
    run_id: 'r',
    campaign_id: 'c-a',
    engine: 'gp',
    seed: 0,
    agent: 'engine',
    model_used: 'none',
    cell_id: null,
    event: name,
    strategy_hash: 'a'.repeat(64),
    drift_delta: null,
    island: null,
    detail: {},
  }
}

function renderScreen(path = '/campaign/c-a/audit') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/campaign/:cid/audit" element={<Audit />} />
      </Routes>
    </MemoryRouter>,
  )
}

const lock = {
  status: 'verified' as const,
  lock: { campaign_id: 'c-a', dsr_min: 0.95 },
  expected_hash: 'b'.repeat(64),
  actual_hash: 'b'.repeat(64),
}

describe('Audit', () => {
  it('pages through the audit log instead of stopping at the first 50 events', async () => {
    mockApi({
      '/api/campaigns/c-a/events': {
        items: [event(1, 'CANDIDATE_SUBMITTED')],
        names: ['CANDIDATE_SUBMITTED', 'DEGRADATION_WARNING'],
        total: 941,
        page: 1,
        size: 50,
        snapshot_at: '2026-09-24T10:00:00+00:00',
      },
      '/api/campaigns/c-a/lock': lock,
    })
    renderScreen()
    expect(await screen.findByText(/941 mục · trang 1\/19/)).toBeTruthy()

    fireEvent.click(screen.getByLabelText('Trang sau'))
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      expect(calls.some(([url]) => String(url).includes('page=2'))).toBe(true)
    })
  })

  it('offers the campaign’s own event names so the filter is never guessed', async () => {
    mockApi({
      '/api/campaigns/c-a/events': {
        items: [event(1, 'DEGRADATION_WARNING')],
        names: ['CANDIDATE_SUBMITTED', 'DEGRADATION_WARNING'],
        total: 1,
        page: 1,
        size: 50,
        snapshot_at: '2026-09-24T10:00:00+00:00',
      },
      '/api/campaigns/c-a/lock': lock,
    })
    renderScreen()
    const filter = (await screen.findByLabelText('Lọc theo loại event')) as HTMLSelectElement
    expect([...filter.options].map((option) => option.value)).toEqual([
      '',
      'CANDIDATE_SUBMITTED',
      'DEGRADATION_WARNING',
    ])
  })

  it('names a lock mismatch instead of only showing a status word', async () => {
    mockApi({
      '/api/campaigns/c-a/events': {
        items: [],
        names: [],
        total: 0,
        page: 1,
        size: 50,
        snapshot_at: '2026-09-24T10:00:00+00:00',
      },
      '/api/campaigns/c-a/lock': { ...lock, status: 'mismatch', actual_hash: 'c'.repeat(64) },
    })
    renderScreen()
    expect(await screen.findByText(/đã bị sửa sau khi mở campaign/)).toBeTruthy()
    expect(screen.getByText('Chưa có audit event.')).toBeTruthy()
  })
})
