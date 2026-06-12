import { useState, useEffect, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { getHeatColor } from '../lib/mock-data.ts'
import { adminApi } from '../lib/api/admin.ts'
import type { AdminMemoryFilters, AdminMemorySummary, ScopeKind } from '../lib/api/types.ts'

type MemoryListShellProps = {
  scope: ScopeKind
  scopeId: string
  query?: string
  type?: string
  project?: string
  refreshKey?: number
  onTotalCount?: (count: number) => void
  onDeleteSuccess?: () => void
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

export function MemoryListShell({ scope, scopeId, query, type, project, refreshKey, onTotalCount, onDeleteSuccess }: MemoryListShellProps) {
  const [items, setItems] = useState<AdminMemorySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [bulkCopyActive, setBulkCopyActive] = useState(false)
  const [bulkTargetScope, setBulkTargetScope] = useState<ScopeKind>('user')
  const [bulkTargetScopeId, setBulkTargetScopeId] = useState('')
  const [bulkCopying, setBulkCopying] = useState(false)

  useEffect(() => {
    setPage(1)
  }, [scope, scopeId, query, type, project, refreshKey])

  useEffect(() => {
    let cancelled = false

    setLoading(true)
    setError(null)

    const filters: AdminMemoryFilters = {
      ...(scopeId ? { scope, scopeId } : {}),
      ...(query ? { query } : {}),
      ...(type ? { type } : {}),
      ...(project ? { project } : {}),
      page,
      pageSize: 20,
    }

    adminApi
      .listMemories(filters)
      .then((response) => {
        if (!cancelled) {
          setItems(response.items)
          setTotalPages(response.total_pages)
          onTotalCount?.(response.total_items)
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
  }, [scope, scopeId, query, type, project, refreshKey, page])

  const allSelected = items.length > 0 && selectedIds.size === items.length

  const handleHeaderCheckbox = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(items.map((m) => m.memory_id)))
    }
  }

  const handleToggleItem = (memoryId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(memoryId)) {
        next.delete(memoryId)
      } else {
        next.add(memoryId)
      }
      return next
    })
  }

  const handleBulkCopy = async () => {
    if (!bulkTargetScopeId.trim() || selectedIds.size === 0) return
    setBulkCopying(true)
    try {
      const ids = [...selectedIds]
      for (const id of ids) {
        await adminApi.copyMemory(id, { target_scope: bulkTargetScope, target_scope_id: bulkTargetScopeId.trim() })
      }
      setSelectedIds(new Set())
      setBulkCopyActive(false)
      setBulkTargetScopeId('')
    } catch {
      // silent
    }
    setBulkCopying(false)
  }

  const handleDelete = async (memoryId: string) => {
    try {
      await adminApi.deleteMemory(memoryId)
      setItems((prev) => prev.filter((m) => m.memory_id !== memoryId))
      onDeleteSuccess?.()
    } catch {
      // silent — no confirmation dialog per spec
    }
  }

  return (
    <section className="panel memory-section" aria-label="Memory list shell">
      <div className="memory-toolbar">
        <label className="checkbox-row">
          <input type="checkbox" checked={allSelected} onChange={handleHeaderCheckbox} />
          <span className="panel-title">Memories</span>
        </label>

        <div className="toolbar-actions">
          <button
            type="button"
            className="button-ghost"
            disabled={selectedIds.size === 0}
            onClick={() => setBulkCopyActive((v) => !v)}
          >
            Copy to user…{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
          </button>
          <div className="legend-stack" aria-label="Heat legend">
            <span className="legend-copy">heat</span>
            <div className="summary-row">
              <div className="legend-bar" aria-hidden="true" />
              <span className="legend-copy">cold → hot</span>
            </div>
          </div>
        </div>

        {bulkCopyActive && selectedIds.size > 0 && (
          <div className="bulk-copy-bar">
            <label className="field-stack">
              <span className="field-label">Target scope</span>
              <select
                className="control-select"
                value={bulkTargetScope}
                onChange={(e) => setBulkTargetScope(e.target.value as ScopeKind)}
              >
                <option value="user">user</option>
                <option value="agent">agent</option>
                <option value="run">run</option>
              </select>
            </label>
            <label className="field-stack">
              <span className="field-label">Target scope id</span>
              <input
                type="text"
                className="control-input mono"
                value={bulkTargetScopeId}
                onChange={(e) => setBulkTargetScopeId(e.target.value)}
                placeholder="e.g. bob"
              />
            </label>
            <button
              type="button"
              className="button"
              disabled={bulkCopying || !bulkTargetScopeId.trim()}
              onClick={handleBulkCopy}
            >
              {bulkCopying ? 'Copying…' : `Copy ${selectedIds.size} memory${selectedIds.size === 1 ? '' : 'ies'}`}
            </button>
            <button type="button" className="button-ghost" onClick={() => setBulkCopyActive(false)}>
              Cancel
            </button>
          </div>
        )}
      </div>

      {loading && <p className="memory-status-text">Loading…</p>}
      {error && <p className="memory-status-text is-error">{error}</p>}

      {!loading && !error && items.length === 0 && (
        <p className="memory-status-text">{scopeId ? 'No memories found for this scope' : 'No memories in the system'}</p>
      )}

      {!loading && !error && items.length > 0 && (
        <div className="memory-grid">
          {items.map((memory) => {
            const heat = memory.popularity?.total_visits ?? 0
            const cardStyle = {
              '--memory-accent': getHeatColor(heat),
            } as CSSProperties
            const memoryType = typeof memory.metadata?.type === 'string' ? memory.metadata.type : null
            const rawAnchor = memory.metadata?.anchor as Record<string, unknown> | undefined
            const memoryAnchor =
              rawAnchor &&
              typeof rawAnchor.type === 'string' &&
              typeof rawAnchor.locator === 'string'
                ? { type: rawAnchor.type, locator: rawAnchor.locator }
                : null

            return (
              <article key={memory.memory_id} className="memory-card" style={cardStyle}>
                <div className="memory-card-top">
                  <div className="memory-stack">
                    <label className="checkbox-cell">
                      <input
                        type="checkbox"
                        aria-label={`Select ${memory.memory_id}`}
                        checked={selectedIds.has(memory.memory_id)}
                        onChange={() => handleToggleItem(memory.memory_id)}
                      />
                      <span className="micro-label">Select memory</span>
                    </label>
                    <div className="scope-tag-row">
                      <span className="scope-tag">
                        {scopeToPrefix(memory.scope)}:{memory.scope_id}
                      </span>
                      {memoryType && <span className="scope-tag type-tag">{memoryType}</span>}
                      {memoryAnchor && (
                        <span className="scope-tag anchor-tag">
                          📎 {memoryAnchor.type}: {memoryAnchor.locator.slice(0, 30)}
                        </span>
                      )}
                    </div>
                  </div>

                  <span className="memory-heat">{heat}</span>
                </div>

                <p className="memory-summary">{memory.content}</p>

                <div className="memory-card-footer">
                  <p className="summary-meta">Created: {formatDate(memory.freshness?.created_at ?? null)}</p>
                  <div className="summary-actions">
                    <Link className="button-text is-link" to={`/memories/${memory.memory_id}`}>
                      Edit
                    </Link>
                    <Link className="button-text is-link" to={`/memories/${memory.memory_id}`}>
                      Decay
                    </Link>
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

      {!loading && totalPages > 1 && (
        <div className="pagination-bar" style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', justifyContent: 'center' }}>
          <button
            type="button"
            className="button-ghost"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Prev
          </button>
          <span style={{ fontSize: '0.85rem', color: 'var(--color-text-muted)' }}>
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="button-ghost"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}
