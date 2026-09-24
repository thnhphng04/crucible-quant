import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'
import { campaign, mockApi, overview } from './test/mockApi'

function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App', () => {
  it('opens the newest campaign when no campaign is in the URL', async () => {
    mockApi({
      '/api/campaigns': [campaign('c-a'), campaign('c-b')],
      '/api/campaigns/c-a/overview': overview('c-a'),
      '/api/campaigns/c-b/overview': overview('c-b'),
    })
    renderApp()
    expect(await screen.findByRole('heading', { level: 1, name: 'c-a' })).toBeTruthy()
  })

  it('follows the selected campaign in both the selector and the tab links', async () => {
    // Regression: the shell used to read `cid` outside its own route, so picking the
    // second campaign left every tab link pointing back at the first one.
    mockApi({
      '/api/campaigns': [campaign('c-a'), campaign('c-b')],
      '/api/campaigns/c-a/overview': overview('c-a'),
      '/api/campaigns/c-b/overview': overview('c-b'),
    })
    renderApp()
    await screen.findByRole('heading', { level: 1, name: 'c-a' })

    const selector = screen.getByLabelText('Campaign') as HTMLSelectElement
    fireEvent.change(selector, { target: { value: 'c-b' } })

    await screen.findByRole('heading', { level: 1, name: 'c-b' })
    expect(selector.value).toBe('c-b')
    expect(screen.getByRole('link', { name: 'Ứng viên' }).getAttribute('href')).toBe(
      '/campaign/c-b/candidates',
    )
    expect(screen.getByRole('link', { name: 'Audit & lock' }).getAttribute('href')).toBe(
      '/campaign/c-b/audit',
    )
  })

  it('says a campaign is unknown instead of showing another campaign’s data', async () => {
    mockApi({
      '/api/campaigns': [campaign('c-a')],
      '/api/campaigns/c-a/overview': overview('c-a'),
    })
    renderApp('/campaign/c-missing/overview')
    expect(await screen.findByText(/không có trong ledger/)).toBeTruthy()
  })

  it('reports an empty ledger rather than a blank screen', async () => {
    mockApi({ '/api/campaigns': [] })
    renderApp()
    expect(await screen.findByText('Ledger chưa có campaign nào.')).toBeTruthy()
  })

  it('offers a retry when the ledger cannot be read', async () => {
    mockApi({})
    renderApp()
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeTruthy()
  })
})
