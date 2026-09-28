import { expect, test } from '@playwright/test'

const config = {
  operational: {},
  research: {
    max_risk_pct: 0.01,
    engines: { gp: 0.5, random: 0.5, quantevolve: 0, simple_loop: 0 },
    campaign: { purpose: 'harness_test', trial_budget: 10 },
    evolve_scope: 'joint',
    seeds: 3,
    data: {
      exchange: 'binance', symbols: ['BTC/USDT'], market: 'spot', timeframe: '1h',
      start: '2020-01-01', end: '2023-01-01', holdout_months: 12,
      second_exchange: null,
    },
    exit: { tp_sl_ratio: 1.1, max_holding_bars: 100 },
    holdout_pass: 1.3,
  },
}

test('Studio creates a campaign and starts its search from the UI', async ({ page }) => {
  let draftCreated = false
  await page.route('**/api/studio/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()
    let body: unknown = {}
    if (path === '/api/studio/capabilities') {
      body = {
        contract_version: 1, session_token: 'test-token', max_workers: 8,
        defaults: config,
        enums: {
          purposes: ['research', 'harness_test'],
          markets: ['spot', 'usdt_m_perpetual'],
          timeframes: ['15m', '1h'], engines: ['gp', 'random'],
        },
      }
    } else if (path === '/api/studio/drafts' && method === 'POST') {
      draftCreated = true
      body = { draft_id: 'draft-test', revision: 1, config, dataset_id: 'dataset-test', state: 'ready' }
    } else if (path === '/api/studio/drafts/draft-test/preview') {
      body = {
        ok: true, problems: [], preview_token: 'preview-test',
        dataset: { id: 'dataset-test', hash: 'a'.repeat(64) },
        lock_summary: { purpose: 'harness_test', exit: config.research.exit },
      }
    } else if (path === '/api/studio/campaigns' && method === 'POST') {
      body = { campaign_id: 'c-studio-test', dataset_id: 'dataset-test', lock_sha256: 'b'.repeat(64) }
    } else if (path === '/api/studio/campaigns/c-studio-test/runs') {
      expect(route.request().headers()['x-studio-token']).toBe('test-token')
      body = { job_id: 'job-test', kind: 'evolve', state: 'running' }
    } else if (path === '/api/studio/jobs/job-test') {
      body = {
        job_id: 'job-test', kind: 'evolve', state: 'running',
        updated_at: '2026-09-28T00:00:00+00:00', progress: { trials: 2, quota: 10 },
      }
    } else {
      await route.fulfill({ status: 404, json: { detail: `unhandled ${path}` } })
      return
    }
    await route.fulfill({ json: body })
  })

  await page.goto('/studio')
  await expect(page.getByRole('heading', { name: 'Campaign Studio' })).toBeVisible()
  await page.getByRole('button', { name: 'Tạo draft' }).click()
  await expect(page.getByText('Draft revision 1 đã lưu.')).toBeVisible()
  expect(draftCreated).toBe(true)
  await page.getByRole('button', { name: 'Xem trước', exact: true }).click()
  await expect(page.getByText('Preview hợp lệ.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Tạo campaign' }).click()
  await expect(page.getByText('Campaign c-studio-test đã tạo.')).toBeVisible()
  await page.getByRole('button', { name: 'Chạy campaign' }).click()
  await expect(page.getByText('Search job đã bắt đầu.')).toBeVisible()
  await expect(page.getByText('2/10')).toBeVisible()
})
