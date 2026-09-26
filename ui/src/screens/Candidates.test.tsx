import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Candidates } from './Candidates'
import { mockApi } from '../test/mockApi'

function row(id: string, extra: Record<string, unknown> = {}) {
  return {
    campaign_id: 'c-a',
    candidate_id: id,
    engine: 'gp',
    seed: 0,
    island: null,
    strategy_hash: 'f'.repeat(64),
    source: 'evolution',
    sharpe_is: 1.234,
    verdict: 'PASS',
    gate_failed: null,
    ts: '2026-09-24T00:15:59+00:00',
    trial_id: 1,
    cell_id: null,
    ...extra,
  }
}

function page(items: unknown[], total: number, current = 1) {
  return { items, total, page: current, size: 50, snapshot_at: '2026-09-24T10:00:00+00:00' }
}

function renderScreen(path = '/campaign/c-a/candidates') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/campaign/:cid/candidates" element={<Candidates />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Candidates', () => {
  it('shows the blocking gate next to a rejected candidate', async () => {
    mockApi({
      '/api/campaigns/c-a/candidates': page(
        [row('gp-0'), row('gp-1', { verdict: 'REJECT', gate_failed: 'g4_pbo', sharpe_is: null })],
        2,
      ),
    })
    renderScreen()
    expect(await screen.findByText('gp-1')).toBeTruthy()
    expect(screen.getByText('g4_pbo')).toBeTruthy()
    expect(screen.getByText('✕ REJECT')).toBeTruthy()
    expect(screen.getAllByText('Chưa có').length).toBeGreaterThan(0)
  })

  it('distinguishes an empty campaign from an empty filter result', async () => {
    mockApi({ '/api/campaigns/c-a/candidates': page([], 0) })
    renderScreen()
    expect(await screen.findByText('Campaign chưa ghi ứng viên nào.')).toBeTruthy()

    mockApi({ '/api/campaigns/c-a/candidates': page([], 0) })
    renderScreen('/campaign/c-a/candidates?engine=random')
    expect(
      await screen.findByText('Không có ứng viên nào khớp bộ lọc hiện tại.'),
    ).toBeTruthy()
  })

  it('asks the server for the next page and disables the edges', async () => {
    mockApi({ '/api/campaigns/c-a/candidates': page([row('gp-0')], 120, 1) })
    renderScreen()
    await screen.findByText('gp-0')
    expect(screen.getByLabelText('Trang trước')).toHaveProperty('disabled', true)

    fireEvent.click(screen.getByLabelText('Trang sau'))
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      expect(calls.some(([url]) => String(url).includes('page=2'))).toBe(true)
    })
  })

  it('only queries once typing pauses', async () => {
    vi.useFakeTimers()
    try {
      mockApi({ '/api/campaigns/c-a/candidates': page([row('gp-0')], 1) })
      renderScreen()
      const search = screen.getByLabelText('Tìm theo candidate ID hoặc strategy hash')
      fireEvent.change(search, { target: { value: 'gp' } })
      fireEvent.change(search, { target: { value: 'gp-0' } })
      const before = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.length
      vi.advanceTimersByTime(400)
      const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      expect(calls.length).toBeLessThanOrEqual(before + 1)
      expect(calls.filter(([url]) => String(url).includes('q=gp&')).length).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})
