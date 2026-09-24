import { expect, test } from '@playwright/test'

const CAMPAIGN = 'c-demo-20260101'
const TABS = ['Tổng quan', 'GP ↔ Random', 'Ứng viên', 'Danh mục', 'Archive', 'Audit & lock']

test('opens on the newest campaign and never offers a write control', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(new RegExp(`/campaign/${CAMPAIGN}/overview$`))
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(CAMPAIGN)

  // Read-only by construction: the only buttons are refresh and paging.
  const labels = await page.getByRole('button').allInnerTexts()
  for (const label of labels) {
    expect(label).not.toMatch(/xóa|sửa|lưu|freeze|abandon|mở holdout/i)
  }
  await expect(page.getByRole('textbox')).toHaveCount(0)
})

test('every tab renders its own screen', async ({ page }, testInfo) => {
  await page.goto(`/campaign/${CAMPAIGN}/overview`)
  for (const tab of TABS) {
    await page.getByRole('link', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(page.getByText('Không thể đọc dữ liệu')).toHaveCount(0)
    // Screenshot the loaded screen, not the skeleton that precedes it.
    await expect(page.getByRole('status')).toHaveCount(0)
    await page.screenshot({
      path: `screenshots/${testInfo.project.name}/${tab.replace(/[^\p{L}\d]+/gu, '-')}.png`,
      fullPage: true,
    })
  }
})

test('the comparison screen states the budget without naming a winner', async ({ page }) => {
  await page.goto(`/campaign/${CAMPAIGN}/comparison`)
  await expect(page.getByRole('heading', { name: 'Ngân sách và tỷ lệ qua ④' })).toBeVisible()
  await expect(page.getByText('gp-s0').first()).toBeVisible()
  await expect(page.getByText('random-s1').first()).toBeVisible()
  await expect(page.getByRole('img', { name: /Sharpe IS và OOS median/ })).toBeVisible()
  await expect(page.getByText(/⚠ random-s1/)).toBeVisible()
  // The arms are shown side by side; nothing on the page ranks one above the other.
  await expect(page.locator('body')).not.toContainText(/thắng cuộc|vượt trội|winner/i)

  await page.goto(`/campaign/${CAMPAIGN}/overview`)
  await expect(page.getByRole('heading', { name: 'Chưa kết luận' })).toBeVisible()
})

test('a candidate traces to its gates, its curve and its stored source', async ({ page }) => {
  await page.goto(`/campaign/${CAMPAIGN}/candidates`)
  await page.getByRole('link', { name: 'gp-s0-000' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('gp-s0-000')
  await expect(page.getByText('g4_pbo')).toBeVisible()
  await expect(page.getByRole('img', { name: /Đường tăng trưởng vốn/ })).toBeVisible()
  await expect(page.getByText('def signal(bar):')).toBeVisible()
})

test('a rejected candidate shows the gate that blocked it', async ({ page }) => {
  await page.goto(`/campaign/${CAMPAIGN}/candidates`)
  const rejected = page.getByRole('row', { name: /gp-s0-001/ })
  await expect(rejected).toContainText('g4_pbo')
  await expect(rejected).toContainText('REJECT')
})

test('switching campaign moves the whole shell, not just the screen', async ({ page }) => {
  await page.goto(`/campaign/${CAMPAIGN}/overview`)
  await page.getByLabel('Campaign', { exact: true }).selectOption('c-demo-20251220')
  await expect(page).toHaveURL(/c-demo-20251220\/overview$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('c-demo-20251220')

  await page.getByRole('link', { name: 'Ứng viên', exact: true }).click()
  await expect(page).toHaveURL(/c-demo-20251220\/candidates/)
  await expect(page.getByText('Campaign chưa ghi ứng viên nào.')).toBeVisible()
})

test('the audit tab pages the log and verifies the lock hash', async ({ page }) => {
  await page.goto(`/campaign/${CAMPAIGN}/audit`)
  await expect(page.getByText('Khớp hash trong ledger')).toBeVisible()
  await page.getByLabel('Lọc theo loại event').selectOption('DEGRADATION_WARNING')
  await expect(page).toHaveURL(/event=DEGRADATION_WARNING/)
  // The demo campaign holds exactly one warning, so the filter must page down to it.
  await expect(page.getByLabel('Phân trang')).toContainText('1 mục · trang 1/1')
})

test('a campaign that is not in the ledger is named as missing', async ({ page }) => {
  await page.goto('/campaign/c-khong-ton-tai/overview')
  await expect(page.getByText(/không có trong ledger/)).toBeVisible()
})
