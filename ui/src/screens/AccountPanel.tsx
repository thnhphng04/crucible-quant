import { LineChart } from '../components/LineChart'
import { Panel, TableWrap } from '../components/Panel'
import { Badge } from '../components/Badge'
import { Empty } from '../components/States'
import { useApi } from '../lib/api'
import { num } from '../lib/format'
import type { AccountData } from '../lib/types'

/**
 * The shared account of one portfolio variant, and where its equity came from (P3-23, ADR-0035).
 *
 * Under `Q = R/d` the slots are not separately funded — they compete for one balance, one margin
 * pool and one 10% risk cap — so the only honest claim this panel can make is the arithmetic one:
 * `vốn ban đầu + Σ đóng góp = equity`. The residual arrives measured from the stored curve, and is
 * shown rather than asserted, so a drift is something the reader can see.
 *
 * The slots are a **table**, not lines. `LineChart`'s legend lays out at a fixed pitch and fits
 * about four entries; ten slots would overlap it into nonsense. The account's own equity is the
 * one line worth drawing.
 */
export function AccountPanel({ cid, portfolioHash }: { cid: string; portfolioHash: string }) {
  const { data } = useApi<AccountData>(`/api/campaigns/${cid}/account/${portfolioHash}`, false)

  if (!data) return null
  if (data.status === 'missing') {
    return (
      <Panel title="Tài khoản chung">
        <Empty text="Chưa có đường equity tài khoản cho variant này." />
      </Panel>
    )
  }

  return (
    <Panel
      title="Tài khoản chung"
      note="Các slot dùng chung một số dư, một pool margin và một trần rủi ro 10%; đóng góp là phép phân rã của equity, không phải tài khoản con."
    >
      <LineChart
        series={[{ name: 'Equity tài khoản', color: '#45d483', values: data.points.map((p) => p.equity) }]}
        labels={data.points.map((p) => p.ts)}
        ariaLabel="Equity của tài khoản chung theo thời gian"
        caption={`Vốn ban đầu ${num(data.initial_cash)} · ${data.points.length} nến`}
        height={220}
        digits={0}
      />
      <TableWrap>
        <table>
          <thead>
            <tr>
              <th scope="col">Slot</th>
              <th scope="col">Đóng góp cuối kỳ</th>
            </tr>
          </thead>
          <tbody>
            {data.slots.map((slot) => (
              <tr key={slot.slot}>
                <td className="mono">{slot.slot}</td>
                <td>{num(slot.final)}</td>
              </tr>
            ))}
            <tr>
              <td>
                <strong>Đối soát</strong>
              </td>
              <td>
                <Badge kind={data.reconciles ? 'pass' : 'reject'}>
                  {data.reconciles ? 'Khớp' : 'Lệch'}
                </Badge>{' '}
                sai số lớn nhất {num(data.max_residual, 6)}
              </td>
            </tr>
          </tbody>
        </table>
      </TableWrap>
      {data.slots.length === 0 ? <Empty text="Chưa có slot nào đóng góp." /> : null}
    </Panel>
  )
}
