import { useState, useEffect, type CSSProperties } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CopyActionShell } from '../components/copy-action-shell.tsx'
import { Panel } from '../components/panel.tsx'
import { computeDecayDisplay, deriveHalfLifeDays } from '../lib/decay.ts'
import { getHeatColor } from '../lib/mock-data.ts'
import { adminApi } from '../lib/api/admin.ts'
import type { AdminMemoryDetail, ScopeKind } from '../lib/api/types.ts'

/** Convert a metadata value to its string representation for editing. */
function metadataValueToString(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

/** Parse a string value back to its typed representation for the API. */
function parseMetadataValue(raw: string): unknown {
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

/** Format a metadata value for read-only display, truncating long strings. */
function formatMetadataDisplay(value: unknown): string {
  if (value === null || value === undefined) return '\u2014'
  if (typeof value === 'string') return value.length > 80 ? value.slice(0, 80) + '\u2026' : value
  return JSON.stringify(value)
}

/** Whether a stringified metadata value needs a textarea instead of a single-line input. */
function isLongFormValue(value: string): boolean {
  return value.length > 80 || value.startsWith('{') || value.startsWith('[')
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

export function MemoryDetailPage() {
  const { memoryId } = useParams()
  const [memory, setMemory] = useState<AdminMemoryDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [editMetadata, setEditMetadata] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)

  useEffect(() => {
    if (!memoryId) return

    let cancelled = false
    setLoading(true)
    setError(null)

    adminApi
      .getMemoryDetail(memoryId)
      .then((result) => {
        if (!cancelled) {
          setMemory(result)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load memory')
          setLoading(false)
        }
      })

    adminApi.recordVisit(memoryId, { reason: 'detail_open' }).catch(() => {
      // silent — visit recording is best-effort
    })

    return () => {
      cancelled = true
    }
  }, [memoryId])

  /** Populate edit state from the current memory snapshot. */
  const startEditing = () => {
    if (!memory) return
    setEditContent(memory.content)
    const initial: Record<string, string> = {}
    if (memory.metadata) {
      for (const [key, value] of Object.entries(memory.metadata)) {
        initial[key] = metadataValueToString(value)
      }
    }
    setEditMetadata(initial)
    setEditing(true)
    setSaveError(null)
    setSaveSuccess(false)
  }

  const cancelEditing = () => {
    setEditing(false)
    setEditContent('')
    setEditMetadata({})
    setSaveError(null)
  }

  /** Parse edited metadata strings back to typed values and send to the API. */
  const handleSave = async () => {
    if (!memoryId || !memory) return
    setSaving(true)
    setSaveError(null)
    setSaveSuccess(false)

    try {
      const updatedMetadata: Record<string, unknown> = { ...memory.metadata }
      for (const [key, strValue] of Object.entries(editMetadata)) {
        if (strValue === '') {
          delete updatedMetadata[key]
        } else {
          updatedMetadata[key] = parseMetadataValue(strValue)
        }
      }
      if (typeof updatedMetadata.type === 'string') {
        updatedMetadata.decay_half_life_days = deriveHalfLifeDays(updatedMetadata.type)
      }

      const updated = await adminApi.updateMemory(memoryId, {
        messages: [{ role: 'user', content: editContent }],
        metadata: updatedMetadata,
      })
      setMemory(updated)
      setEditing(false)
      setEditContent('')
      setEditMetadata({})
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)

      adminApi.recordVisit(memoryId, { reason: 'edit_save' }).catch(() => {
        // silent — visit recording is best-effort
      })
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save memory')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="detail-layout">
        <p className="memory-status-text">Loading…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="detail-layout">
        <p className="memory-status-text is-error">{error}</p>
      </div>
    )
  }

  if (!memory) {
    return (
      <div className="detail-layout">
        <p className="memory-status-text">Memory not found</p>
      </div>
    )
  }

  const heat = memory.popularity?.total_visits ?? 0
  const decay = computeDecayDisplay(memory.freshness)
  const accentStyle = { '--memory-accent': getHeatColor(heat) } as CSSProperties

  return (
    <div className="detail-layout">
      <section className="hero-panel" style={accentStyle}>
        <div className="hero-header">
          <div className="section-stack">
            <p className="eyebrow">memory detail</p>
            <h1 className="hero-title">{memory.content}</h1>
            <p className="hero-copy">
              This route keeps the detail, copy, and raw decay inputs split into stable
              cards so later API wiring does not need to rework the mock-aligned layout.
            </p>
          </div>

          <div className="summary-row">
            <span className="count-badge">heat {heat}</span>
            <Link className="button-ghost" to="/memories">
              Back to memories
            </Link>
          </div>
        </div>

        <div className="scope-tag-row" aria-label="Memory scope tags">
          <span className="scope-tag">
            {scopeToPrefix(memory.scope)}:{memory.scope_id}
          </span>
        </div>
      </section>

      <div className="summary-grid">
        <div className="detail-stack">
          <Panel
            eyebrow="detail"
            title="Memory content"
            subtitle="Content editing with metadata managed in a separate panel below."
          >
            <div className="detail-grid">
              <article className="detail-card">
                <p className="micro-label">Content</p>
                {editing ? (
                  <>
                    <textarea
                      className="control-textarea"
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      disabled={saving}
                      rows={4}
                    />
                    {saveError && <p className="memory-status-text is-error">{saveError}</p>}
                    <div style={{ display: 'inline-flex', gap: 'var(--space-3)', marginTop: 'var(--space-3)' }}>
                      <button className="button" onClick={handleSave} disabled={saving}>
                        {saving ? 'Saving\u2026' : 'Save'}
                      </button>
                      <button className="button-ghost" onClick={cancelEditing} disabled={saving}>
                        Cancel
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <h2 className="detail-title">{memory.content}</h2>
                    <div style={{ marginTop: 'var(--space-3)' }}>
                      <button className="button-ghost" onClick={startEditing}>
                        Edit
                      </button>
                    </div>
                  </>
                )}
                {saveSuccess && (
                  <p style={{ margin: 'var(--space-2) 0 0', color: 'var(--color-accent)', fontSize: '0.85rem' }}>
                    Saved successfully
                  </p>
                )}
                <p className="detail-copy">
                  The future detail editor can mount here without changing the card shell, since the
                  locked admin contract already separates content, metadata, and audit blocks.
                </p>
              </article>
            </div>
          </Panel>

          <Panel eyebrow="metadata" title="All metadata fields">
            <article className="detail-card">
              <p className="micro-label">{editing ? 'Edit metadata' : 'Metadata'}</p>
              {editing ? (
                <dl className="detail-key-value">
                  {Object.entries(editMetadata).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>
                        {isLongFormValue(value) ? (
                          <textarea
                            className="control-textarea"
                            value={value}
                            onChange={(e) =>
                              setEditMetadata((prev) => ({ ...prev, [key]: e.target.value }))
                            }
                            disabled={saving}
                            rows={3}
                          />
                        ) : (
                          <input
                            className="control-input mono"
                            value={value}
                            onChange={(e) =>
                              setEditMetadata((prev) => ({ ...prev, [key]: e.target.value }))
                            }
                            disabled={saving}
                          />
                        )}
                      </dd>
                    </div>
                  ))}
                  {Object.keys(editMetadata).length === 0 && (
                    <p className="memory-status-text">No metadata fields to edit</p>
                  )}
                </dl>
              ) : memory.metadata ? (
                <dl className="detail-key-value">
                  {Object.entries(memory.metadata).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{formatMetadataDisplay(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="memory-status-text">No metadata</p>
              )}
            </article>
          </Panel>

          <CopyActionShell
            sourceMemoryId={memory.memory_id}
            sourceLabel={memory.memory_id}
            onCopy={() => {}}
          />
        </div>

        <div className="detail-stack">
          <Panel
            eyebrow="signals"
            title="Decay analysis"
            subtitle="Recency and half-life computed from raw backend fields using the plugin-authority decay formulas."
          >
            <div className="detail-grid">
              <article className="detail-card">
                <p className="micro-label">Raw inputs</p>
                <dl className="detail-key-value">
                  <div>
                    <dt>created_at</dt>
                    <dd>{memory.freshness?.created_at ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>decay_half_life_days</dt>
                    <dd>{memory.freshness?.decay_half_life_days ?? '— (derived from type)'}</dd>
                  </div>
                  <div>
                    <dt>last_visited_at</dt>
                    <dd>{memory.freshness?.last_visited_at ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>ttl_expires_at</dt>
                    <dd>{memory.freshness?.ttl_expires_at ?? '—'}</dd>
                  </div>
                </dl>
              </article>

              <article className="detail-card">
                <p className="micro-label">Computed decay</p>
                <dl className="detail-key-value">
                  <div>
                    <dt>half_life_days (used)</dt>
                    <dd>{decay.halfLifeDays.toFixed(1)}</dd>
                  </div>
                  <div>
                    <dt>age (days)</dt>
                    <dd>{decay.ageDays.toFixed(1)}</dd>
                  </div>
                  <div>
                    <dt>recency score</dt>
                    <dd>{decay.recency.toFixed(4)}</dd>
                  </div>
                  <div>
                    <dt>never_visited</dt>
                    <dd>{decay.neverVisited ? 'Yes' : 'No'}</dd>
                  </div>
                </dl>
              </article>
            </div>
          </Panel>

          <Panel
            eyebrow="route shell"
            title="Available next actions"
            subtitle="The scaffold keeps later CRUD and visit wiring visible without pretending the behavior is implemented yet."
          >
            <div className="scope-tag-row">
              <span className="detail-stat">detail_open visit</span>
              <span className="detail-stat">edit_save visit</span>
              <span className="detail-stat">copy_source visit</span>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
