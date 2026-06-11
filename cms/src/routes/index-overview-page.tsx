import { useEffect, useState } from 'react'
import { Panel } from '../components/panel.tsx'
import { adminApi } from '../lib/api/admin.ts'
import type { AdminIndexOverviewResponse } from '../lib/api/types.ts'

export function IndexOverviewPage() {
  const [data, setData] = useState<AdminIndexOverviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    adminApi
      .getIndexOverview()
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load index overview')
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return (
      <div className="index-layout">
        <section className="hero-panel">
          <div className="section-stack">
            <p className="eyebrow">index overview</p>
            <h1 className="hero-title">Loading current process state…</h1>
          </div>
        </section>
      </div>
    )
  }

  if (error) {
    return (
      <div className="index-layout">
        <section className="hero-panel">
          <div className="section-stack">
            <p className="eyebrow">index overview</p>
            <h1 className="hero-title">Failed to load index overview</h1>
            <p className="hero-copy">{error}</p>
          </div>
        </section>
      </div>
    )
  }

  if (!data) return null

  const statCards = [
    {
      title: 'Roots',
      value: String(data.roots.length),
      copy: `${data.roots.length} watched root${data.roots.length === 1 ? '' : 's'} with file and chunk tracking.`,
    },
    {
      title: 'Jobs',
      value: String(data.jobs.length),
      copy: `Queue status, timing, and backend result placeholders across ${data.jobs.length} job${data.jobs.length === 1 ? '' : 's'}.`,
    },
    {
      title: 'Files',
      value: String(data.files.length),
      copy: `Chunk counts, languages, and summary embedding state across ${data.files.length} file${data.files.length === 1 ? '' : 's'}.`,
    },
  ]

  return (
    <div className="index-layout">
      <section className="hero-panel">
        <div className="section-stack">
          <p className="eyebrow">index overview</p>
          <h1 className="hero-title">Current process state deserves its own board.</h1>
          <p className="hero-copy">
            This route keeps roots, jobs, files, limits, and visibility inputs separate from the
            memory-management shell while preserving the same mock-aligned palette.
          </p>
          {data.limits.current_process_state_only && (
            <p className="hero-copy" style={{ marginTop: '0.5rem', fontStyle: 'italic' }}>
              Current process state only — index data is not durably persisted across server
              restarts.
            </p>
          )}
        </div>

        <div className="scope-tag-row" role="list" aria-label="Index payload groups">
          <span className="surface-tag" role="listitem">
            roots
          </span>
          <span className="surface-tag" role="listitem">
            jobs
          </span>
          <span className="surface-tag" role="listitem">
            files
          </span>
          <span className="surface-tag" role="listitem">
            visibility_inputs
          </span>
        </div>
      </section>

      <div className="stat-grid">
        {statCards.map((card) => (
          <article key={card.title} className="stat-card">
            <p className="eyebrow">index active</p>
            <p className="stat-value">{card.value}</p>
            <h2 className="panel-title">{card.title}</h2>
            <p className="muted-copy">{card.copy}</p>
          </article>
        ))}
      </div>

      <div className="summary-grid">
        <div className="detail-stack">
          <Panel
            eyebrow="roots and jobs"
            title="Index activity"
            subtitle="Watcher status, job lifecycles, and current-process metadata from the live backend."
          >
            <div className="timeline">
              <div className="info-item">
                <p className="info-title">roots</p>
              </div>
              {data.roots.length > 0 ? (
                data.roots.slice(0, 5).map((root) => (
                  <div key={root.root} className="info-item" style={{ paddingLeft: '1rem' }}>
                    <p className="info-title">{root.root}</p>
                    <p className="muted-copy">
                      {root.total_files} files · {root.total_chunks} chunks
                      {root.watcher_active ? ' · watcher active' : ''}
                      {root.last_job_id ? ` · last job: ${root.last_job_id}` : ''}
                    </p>
                  </div>
                ))
              ) : (
                <div className="info-item">
                  <p className="muted-copy">No roots configured</p>
                </div>
              )}
              {data.roots.length > 5 && (
                <div className="info-item">
                  <p className="muted-copy" style={{ fontStyle: 'italic' }}>
                    … and {data.roots.length - 5} more root{data.roots.length - 5 === 1 ? '' : 's'}
                  </p>
                </div>
              )}

              <div className="info-item" style={{ marginTop: '1rem' }}>
                <p className="info-title">jobs</p>
              </div>
              {data.jobs.length > 0 ? (
                data.jobs.slice(0, 10).map((job) => (
                  <div key={job.job_id} className="info-item" style={{ paddingLeft: '1rem' }}>
                    <p className="info-title">{job.job_id}</p>
                    <p className="muted-copy">
                      status: {job.status}
                      {job.queued_at
                        ? ` · queued: ${new Date(job.queued_at).toLocaleString()}`
                        : ''}
                      {job.completed_at
                        ? ` · completed: ${new Date(job.completed_at).toLocaleString()}`
                        : ''}
                      {job.errors && job.errors.length > 0
                        ? ` · ${job.errors.length} error(s)`
                        : ''}
                    </p>
                  </div>
                ))
              ) : (
                <div className="info-item">
                  <p className="muted-copy">No jobs recorded</p>
                </div>
              )}
              {data.jobs.length > 10 && (
                <div className="info-item">
                  <p className="muted-copy" style={{ fontStyle: 'italic' }}>
                    … and {data.jobs.length - 10} more job{data.jobs.length - 10 === 1 ? '' : 's'}
                  </p>
                </div>
              )}

              <div className="info-item" style={{ marginTop: '1rem' }}>
                <p className="info-title">files</p>
              </div>
              {data.files.length > 0 ? (
                data.files.slice(0, 8).map((file) => (
                  <div
                    key={`${file.root}:${file.file_path}`}
                    className="info-item"
                    style={{ paddingLeft: '1rem' }}
                  >
                    <p className="info-title">{file.file_path}</p>
                    <p className="muted-copy">
                      {file.chunk_count} chunk{file.chunk_count === 1 ? '' : 's'}
                      {file.language ? ` · ${file.language}` : ''}
                      {file.has_summary_embedding ? ' · has summary embedding' : ''}
                      {file.last_indexed_at
                        ? ` · ${new Date(file.last_indexed_at).toLocaleDateString()}`
                        : ''}
                    </p>
                  </div>
                ))
              ) : (
                <div className="info-item">
                  <p className="muted-copy">No files indexed</p>
                </div>
              )}
              {data.files.length > 8 && (
                <div className="info-item">
                  <p className="muted-copy" style={{ fontStyle: 'italic' }}>
                    … and {data.files.length - 8} more file
                    {data.files.length - 8 === 1 ? '' : 's'}
                  </p>
                </div>
              )}
            </div>
          </Panel>
        </div>

        <div className="detail-stack">
          <Panel
            eyebrow="visibility inputs"
            title="Process-only visibility"
            subtitle="Limits and visibility readouts for the current process state."
          >
            <dl className="detail-key-value">
              <div>
                <dt>current_process_state_only</dt>
                <dd>{String(data.limits.current_process_state_only)}</dd>
              </div>
              <div>
                <dt>generated_at</dt>
                <dd>{new Date(data.visibility_inputs.generated_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt>root_count</dt>
                <dd>{data.visibility_inputs.root_count}</dd>
              </div>
              <div>
                <dt>file_count</dt>
                <dd>{data.visibility_inputs.file_count}</dd>
              </div>
              <div>
                <dt>chunk_count</dt>
                <dd>{data.visibility_inputs.chunk_count}</dd>
              </div>
            </dl>
          </Panel>
        </div>
      </div>
    </div>
  )
}
