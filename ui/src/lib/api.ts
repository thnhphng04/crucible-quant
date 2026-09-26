import { useCallback, useEffect, useRef, useState } from 'react'

/** How often a visible tab re-reads the ledger (DESIGN: polling only while visible). */
export const POLL_MS = 5000

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { accept: 'application/json' } })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail || `HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export interface ApiState<T> {
  data: T | undefined
  error: string
  loading: boolean
  reload: () => void
}

/**
 * Reads one endpoint and, when `poll` is set, refreshes it while the tab is visible.
 * A failed refresh keeps the data already on screen and only surfaces the error.
 */
export function useApi<T>(path: string, poll = true): ApiState<T> {
  const [data, setData] = useState<T | undefined>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const current = useRef(path)

  const read = useCallback(
    (showLoading: boolean) => {
      if (showLoading) setLoading(true)
      return getJson<T>(path)
        .then((value) => {
          if (current.current !== path) return
          setData(value)
          setError('')
        })
        .catch((cause: unknown) => {
          if (current.current !== path) return
          setError(cause instanceof Error ? cause.message : String(cause))
        })
        .finally(() => {
          if (current.current === path) setLoading(false)
        })
    },
    [path],
  )

  useEffect(() => {
    current.current = path
    void read(true)
    if (!poll) return
    const timer = setInterval(() => {
      if (!document.hidden) void read(false)
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [path, poll, read])

  return { data, error, loading, reload: () => void read(false) }
}

/** Keeps a text input responsive while the query it drives is only sent after a pause. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return settled
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · QuantCrucible Review`
  }, [title])
}
