import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import s from './ui.module.css'
import { useDocumentTitle } from './lib/api'
import type { CampaignRow } from './lib/types'
import { Empty } from './components/States'

export const TABS: readonly (readonly [string, string])[] = [
  ['overview', 'Tổng quan'],
  ['comparison', 'GP ↔ Random'],
  ['candidates', 'Ứng viên'],
  ['portfolios', 'Danh mục'],
  ['archive', 'Archive'],
  ['audit', 'Audit & lock'],
]

/**
 * The shell is mounted *under* `/campaign/:cid`, so `cid` here is the campaign the
 * screens are actually showing — the selector and the tab links can never drift
 * apart from the route.
 */
export function Shell({ campaigns }: { campaigns: CampaignRow[] }) {
  const { cid } = useParams()
  const navigate = useNavigate()
  useDocumentTitle(cid ?? 'Review')

  const known = campaigns.some((campaign) => campaign.campaign_id === cid)
  return (
    <div className={s.shell}>
      <aside className={s.side}>
        <div className={s.brand}>
          QuantCrucible
          <small>Không gian review · chỉ đọc</small>
        </div>
        <label className={s.muted} htmlFor="campaign">
          Campaign
        </label>
        <select
          id="campaign"
          className={s.select}
          value={known ? cid : ''}
          onChange={(event) => navigate(`/campaign/${event.target.value}/overview`)}
        >
          {known ? null : <option value="">— chọn campaign —</option>}
          {campaigns.map((campaign) => (
            <option key={campaign.campaign_id} value={campaign.campaign_id}>
              {campaign.campaign_id} · {campaign.status}
            </option>
          ))}
        </select>
        <nav className={s.nav} aria-label="Màn hình campaign">
          {TABS.map(([id, label]) => (
            <NavLink
              key={id}
              to={`/campaign/${cid}/${id}`}
              className={({ isActive }) => (isActive ? s.active : '')}
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className={s.main}>
        {known || campaigns.length === 0 ? (
          <Outlet />
        ) : (
          <Empty text={`Campaign “${cid}” không có trong ledger.`} />
        )}
      </main>
    </div>
  )
}
