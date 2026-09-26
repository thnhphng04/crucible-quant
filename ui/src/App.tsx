import { Navigate, Route, Routes } from 'react-router-dom'
import s from './ui.module.css'
import { useApi } from './lib/api'
import type { CampaignRow } from './lib/types'
import { Shell } from './Shell'
import { Empty, ErrorBox, Loading } from './components/States'
import { Archive } from './screens/Archive'
import { Audit } from './screens/Audit'
import { Candidate } from './screens/Candidate'
import { Candidates } from './screens/Candidates'
import { Comparison } from './screens/Comparison'
import { Overview } from './screens/Overview'
import { Portfolios } from './screens/Portfolios'

export default function App() {
  // The campaign list is read once: opening a campaign is a navigation, not a refresh.
  const { data, error, reload } = useApi<CampaignRow[]>('/api/campaigns', false)

  if (error) {
    return (
      <main className={s.main}>
        <ErrorBox text={error} onRetry={reload} />
      </main>
    )
  }
  if (!data) {
    return (
      <main className={s.main}>
        <Loading text="Đang mở ledger ở chế độ chỉ đọc…" />
      </main>
    )
  }

  const landing = data[0]?.campaign_id
  return (
    <Routes>
      <Route path="/campaign/:cid" element={<Shell campaigns={data} />}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<Overview />} />
        <Route path="comparison" element={<Comparison />} />
        <Route path="candidates" element={<Candidates />} />
        <Route path="candidate/:candidateId" element={<Candidate />} />
        <Route path="portfolios" element={<Portfolios />} />
        <Route path="archive" element={<Archive />} />
        <Route path="audit" element={<Audit />} />
        <Route path="*" element={<Navigate to="overview" replace />} />
      </Route>
      <Route
        path="*"
        element={
          landing ? (
            <Navigate to={`/campaign/${landing}/overview`} replace />
          ) : (
            <main className={s.main}>
              <Empty text="Ledger chưa có campaign nào." />
            </main>
          )
        }
      />
    </Routes>
  )
}
