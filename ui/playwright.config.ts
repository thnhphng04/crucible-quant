import { defineConfig, devices } from '@playwright/test'

const PORT = 8766
const BASE_URL = `http://127.0.0.1:${PORT}`

/**
 * The smoke run serves a throwaway demo ledger, never the repository's own `ledger/`,
 * so it can assert on concrete numbers and still never touch real research data.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    // Screenshots are the deliverable here, so keep them on success too.
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'compact-1024',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 800 } },
    },
  ],
  webServer: {
    command: `uv run python scripts/review_demo_server.py --port ${PORT} --static ui/dist`,
    cwd: '..',
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
