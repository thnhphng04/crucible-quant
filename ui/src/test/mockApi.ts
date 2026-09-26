import { vi } from 'vitest'

/** A `fetch` that answers from a path→payload table; anything else becomes a 404. */
export function mockApi(routes: Record<string, unknown>): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      // Longest prefix wins, so `/api/campaigns` never swallows `/api/campaigns/x/overview`.
      const key = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((candidate) => url === candidate || url.startsWith(candidate))
      if (key === undefined) {
        return new Response(JSON.stringify({ detail: `không có route: ${url}` }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify(routes[key]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
}

export function campaign(id: string, overrides: Record<string, unknown> = {}) {
  return {
    campaign_id: id,
    started_at: '2026-09-23T09:09:57+00:00',
    holdout_range: '2025-01-01/2026-01-01',
    lock_hash: 'a'.repeat(64),
    holdout_lock_hash: null,
    status: 'OPEN',
    purpose: 'harness_test',
    trial_budget: 600,
    abandoned_at: null,
    abandonment_reason: null,
    trials: 10,
    events: 20,
    last_activity: '2026-09-24T00:15:59+00:00',
    ...overrides,
  }
}

export function overview(id: string, overrides: Record<string, unknown> = {}) {
  return {
    campaign: campaign(id),
    counts: { trials: 10, candidates: 9, passed: 4, events: 20, warnings: 0 },
    last_activity: '2026-09-24T00:15:59+00:00',
    protocol: { protocol: { version: 3 } },
    verdict: {
      outcome: 'incomplete',
      label: 'Chưa kết luận',
      reason: 'Đã dùng 10/600 trial; các arm chưa cùng ngân sách.',
    },
    snapshot_at: '2026-09-24T10:00:00+00:00',
    ...overrides,
  }
}
