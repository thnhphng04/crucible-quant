import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { campaign } from '../test/mockApi'
import type { StudioCapabilities, StudioDraftConfig } from '../lib/types'

const config: StudioDraftConfig = {
  operational: {},
  research: {
    max_risk_pct: 0.01,
    engines: { gp: 0.5, random: 0.5, quantevolve: 0, simple_loop: 0 },
    campaign: { purpose: 'harness_test', trial_budget: 600 },
    evolve_scope: 'joint',
    seeds: 3,
    data: {
      exchange: 'binance', second_exchange: 'gate',
      symbols: ['BTC/USDT', 'ETH/USDT'], market: 'spot', timeframe: '1h',
      start: '2024-01-01', end: null, holdout_months: 12,
    },
    exit: { tp_sl_ratio: 1.1, max_holding_bars: 100 },
    holdout_pass: 1.3,
  },
}

const capabilities: StudioCapabilities = {
  contract_version: 1,
  session_token: 'studio-token',
  max_workers: 4,
  docker_available: true,
  defaults: config,
  enums: {
    purposes: ['research', 'harness_test'],
    markets: ['spot', 'usdt_m_perpetual'],
    timeframes: ['15m', '1h'],
    engines: ['gp', 'random'],
  },
}

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={['/studio']}>
      <App />
    </MemoryRouter>,
  )
}

describe('Studio', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('keeps review history visible when Studio API is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/campaigns') {
          return Response.json([campaign('c-open')])
        }
        return Response.json({ detail: 'missing' }, { status: 404 })
      }),
    )

    renderStudio()

    expect(await screen.findByText(/Studio API chưa khả dụng/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'c-open' })).toBeTruthy()
  })

  it('sends the Studio token for draft and campaign mutations', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        calls.push({ url, init })
        if (url === '/api/campaigns') return Response.json([campaign('c-open')])
        if (url === '/api/studio/capabilities') return Response.json(capabilities)
        if (url === '/api/studio/drafts') {
          return Response.json({ draft_id: 'd-1', revision: 1, config, dataset_id: null, state: 'editing' })
        }
        if (url === '/api/studio/drafts/d-1/preview') {
          return Response.json({
            ok: true,
            problems: [],
            dataset: { id: 'ds-1', hash: 'a'.repeat(64), coverage: {} },
            lock_summary: { purpose: 'harness_test' },
            quota: { trials: 600 },
            preview_token: 'preview-token',
            expires_at: '2026-09-27T12:00:00Z',
          })
        }
        if (url === '/api/studio/campaigns') {
          return Response.json({ campaign_id: 'c-new', lock_sha256: 'b'.repeat(64), dataset_id: 'ds-1' })
        }
        if (url === '/api/studio/campaigns/c-new/runs') {
          return Response.json({ job_id: 'job-run', state: 'queued', kind: 'evolve' })
        }
        if (url === '/api/studio/jobs/job-run') {
          return Response.json({
            job_id: 'job-run',
            state: 'running',
            kind: 'evolve',
            phase: 'search',
            updated_at: '2026-09-27T12:05:00Z',
            progress: { trials: 3, quota: 600 },
          })
        }
        return Response.json({ detail: `missing ${url}` }, { status: 404 })
      }),
    )

    renderStudio()
    await screen.findByRole('heading', { name: 'Campaign Studio' })

    fireEvent.click(screen.getByRole('button', { name: 'Tạo draft' }))
    expect(await screen.findByText('Draft revision 1 đã lưu.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Xem trước' }))
    expect(await screen.findByText('Preview hợp lệ.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Tạo campaign' }))
    expect(await screen.findByText('Campaign c-new đã tạo.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Chạy campaign' }))
    expect(await screen.findByText('Search job đã bắt đầu.')).toBeTruthy()
    expect(await screen.findByText('running')).toBeTruthy()

    const mutations = calls.filter((call) => call.init?.method)
    expect(mutations).toHaveLength(4)
    for (const call of mutations) {
      expect((call.init?.headers as Record<string, string>)['x-studio-token']).toBe('studio-token')
      expect((call.init?.headers as Record<string, string>)['content-type']).toBe('application/json')
    }
    expect(calls.some((call) => call.url === '/api/studio/campaigns/c-new/runs')).toBe(true)
  })

  it('shows field-level preview errors from the server', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/campaigns') return Response.json([])
        if (url === '/api/studio/capabilities') return Response.json(capabilities)
        if (url === '/api/studio/drafts') {
          return Response.json({ draft_id: 'd-1', revision: 1, config, dataset_id: null, state: 'editing' })
        }
        if (url === '/api/studio/drafts/d-1/preview') {
          return Response.json({
            ok: false,
            problems: [
              {
                path: 'research.data.symbols',
                code: 'MISSING_SECOND_SOURCE',
                message: 'Thiếu coverage nguồn thứ hai cho ETH/USDT',
              },
            ],
          })
        }
        return Response.json({ detail: `missing ${url}` }, { status: 404 })
      }),
    )

    renderStudio()
    await screen.findByRole('heading', { name: 'Campaign Studio' })
    fireEvent.click(screen.getByRole('button', { name: 'Tạo draft' }))
    await screen.findByText('Draft revision 1 đã lưu.')
    fireEvent.click(screen.getByRole('button', { name: 'Xem trước' }))

    expect(await screen.findByText(/Thiếu coverage nguồn thứ hai cho ETH\/USDT/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Tạo campaign' }).hasAttribute('disabled')).toBe(true)
  })
})
