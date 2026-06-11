import { useState, useEffect, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { getHeatColor } from '../lib/mock-data.ts'
import { adminApi } from '../lib/api/admin.ts'
import type { AdminMemorySummary, ScopeKind } from '../lib/api/types.ts'

type MemoryListShellProps = {
  scope: ScopeKind
  scopeId: string
}

function scopeToPrefix(scope: ScopeKind): string {
  switch (scope) {
    case 'user':
      return 'u'
    case 'agent':
      return 'a'
    case 'run':
      return 'r'
  }
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—'
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString('en-GB', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })
  } catch {
    return dateStr
  }
}

export function MemoryListShell({ scope, scopeId }: MemoryListShellProps) {
  const [items, setItems] = useState<AdminMemorySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    adminApi
      .listMemories({ scope, scopeId, page: 1, pageSize: 20 })
      .then((response) => {
        if (!cancelled) {
          setItems(response.items)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load memories')
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [scope, scopeId])

  const handleDelete = async (memoryId: string) => {
    try {
      await adminApi.deleteMemory(memoryId)
      setItems((prev) => prev.filter((m) => m.memory_id !== memoryId))
    } catch {
      // silent — no confirmation dialog per spec
    }
  }

  return (
    <section className="panel memory-section" aria-label="Memory list shell">
      <div className="memory-toolbar">
        <label className="checkbox-row">
          <input type="checkbox" />
          <span className="panel-title">Memories</span>
        </label>

        <div className="toolbar-actions">
          <button type="button" className="button-ghost" disabled>
            Copy to user…
          </button>
          <div className="legend-stack" aria-label="Heat legend">
            <span className="legend-copy">heat</span>
            <div className="summary-row">
              <div className="legend-bar" aria-hidden="true" />
              <span className="legend-copy">cold → hot</span>
            </div>
          </div>
        </div>
      </div>

      {loading && <p className="memory-status-text">Loading…</p>}
      {error && <p className="memory-status-text is-error">{error}</p>}

      {!loading && !error && (
        <div className="memory-grid">
          {items.map((memory) => {
            const heat = memory.popularity?.total_visits ?? 0
            const cardStyle = {
              '--memory-accent': getHeatColor(heat),
            } as CSSProperties

            return (
              <article key={memory.memory_id} className="memory-card" style={cardStyle}>
                <div className="memory-card-top">
                  <div className="memory-stack">
                    <label className="checkbox-cell">
                      <input type="checkbox" aria-label={`Select ${memory.memory_id}`} />
                      <span className="micro-label">Select memory</span>
                    </label>
                    <div className="scope-tag-row">
                      <span className="scope-tag">
                        {scopeToPrefix(memory.scope)}:{memory.scope_id}
                      </span>
                    </div>
                  </div>

                  <span className="memory-heat">{heat}</span>
                </div>

                <p className="memory-summary">{memory.content}</p>

                <div className="memory-card-footer">
                  <p className="summary-meta">{formatDate(memory.freshness?.created_at ?? null)}</p>
                  <div className="summary-actions">
                    <Link className="button-text is-link" to={`/memories/${memory.memory_id}`}>
                      Edit
                    </Link>
                    <button type="button" className="button-text">
                      Decay
                    </button>
                    <button
                      type="button"
                      className="button-text is-danger"
                      onClick={() => handleDelete(memory.memory_id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
