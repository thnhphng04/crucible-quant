import s from '../ui.module.css'
import { num } from '../lib/format'

export function Pagination({
  page,
  size,
  total,
  onPage,
}: {
  page: number
  size: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(Math.ceil(total / size), 1)
  return (
    <nav className={s.pagination} aria-label="Phân trang">
      <span className={s.muted}>
        {num(total)} mục · trang {num(page)}/{num(pages)}
      </span>
      <button
        className={s.button}
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
        aria-label="Trang trước"
      >
        ← Trước
      </button>
      <button
        className={s.button}
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
        aria-label="Trang sau"
      >
        Sau →
      </button>
    </nav>
  )
}
