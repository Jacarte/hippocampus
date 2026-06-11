import type { CSSProperties } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CopyActionShell } from '../components/copy-action-shell.tsx'
import { Panel } from '../components/panel.tsx'
import { computeDecayDisplay } from '../lib/decay.ts'
import { findReferenceMemory, getHeatColor } from '../lib/mock-data.ts'

export function MemoryDetailPage() {
  const { memoryId } = useParams()
  const memory = findReferenceMemory(memoryId)
  const decay = computeDecayDisplay(memory)
  const accentStyle = { '--memory-accent': getHeatColor(memory.heat) } as CSSProperties

  return (
    <div className="detail-layout">
      <section className="hero-panel" style={accentStyle}>
        <div className="hero-header">
          <div className="section-stack">
            <p className="eyebrow">memory detail</p>
            <h1 className="hero-title">{memory.summary}</h1>
            <p className="hero-copy">
              This route keeps the detail, copy, audit, and raw decay inputs split into stable
              cards so later API wiring does not need to rework the mock-aligned layout.
            </p>
          </div>

          <div className="summary-row">
            <span className="count-badge">heat {memory.heat}</span>
            <Link className="button-ghost" to="/memories">
              Back to memories
            </Link>
          </div>
        </div>

        <div className="scope-tag-row" aria-label="Memory scope tags">
          {memory.scopeTags.map((tag) => (
            <span key={`${memory.id}-${tag.prefix}-${tag.value}`} className="scope-tag">
              {tag.prefix}:{tag.value}
            </span>
          ))}
        </div>
      </section>

      <div className="summary-grid">
        <div className="detail-stack">
          <Panel
            eyebrow="detail"
            title="Memory content and audit"
            subtitle="Structured cards keep persisted content, audit provenance, and later visit hooks separate from copy and decay actions."
          >
            <div className="detail-grid">
              <article className="detail-card">
                <p className="micro-label">Content</p>
                <h2 className="detail-title">{memory.summary}</h2>
                <p className="detail-copy">
                  The future detail editor can mount here without changing the card shell, since the
                  locked admin contract already separates content, metadata, and audit blocks.
                </p>
              </article>

              <article className="detail-card">
                <p className="micro-label">Audit</p>
                <div className="timeline">
                  <div className="timeline-item">
                    <p className="timeline-title">impersonated_by=admin</p>
                    <p className="timeline-copy">Reserved for all CMS write flows.</p>
                  </div>
                  <div className="timeline-item">
                    <p className="timeline-title">copied_from=null</p>
                    <p className="timeline-copy">Copy provenance will render here later.</p>
                  </div>
                </div>
              </article>
            </div>
          </Panel>

          <CopyActionShell sourceLabel={memory.id} />
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
                    <dd>{memory.createdAt}</dd>
                  </div>
                  <div>
                    <dt>decay_half_life_days</dt>
                    <dd>{memory.decayHalfLifeDays ?? '— (derived from type)'}</dd>
                  </div>
                  <div>
                    <dt>last_visited_at</dt>
                    <dd>{memory.lastVisitedAt ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>ttl_expires_at</dt>
                    <dd>{memory.ttlExpiresAt ?? '—'}</dd>
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
