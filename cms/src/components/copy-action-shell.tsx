import { useState } from 'react'
import { adminApi } from '../lib/api/admin.ts'
import type { ScopeKind } from '../lib/api/types.ts'
import { Panel } from './panel.tsx'

type CopyActionShellProps = {
  sourceLabel: string
  sourceMemoryId: string
  onCopy: () => void
}

export function CopyActionShell({ sourceLabel, sourceMemoryId, onCopy }: CopyActionShellProps) {
  const [targetScope, setTargetScope] = useState<ScopeKind>('user')
  const [targetScopeId, setTargetScopeId] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')

  const canCopy = targetScopeId.trim().length > 0 && status !== 'loading'

  const handleCopy = async () => {
    if (!canCopy) return

    setStatus('loading')

    try {
      await adminApi.copyMemory(sourceMemoryId, {
        target_scope: targetScope,
        target_scope_id: targetScopeId.trim(),
      })

      await adminApi.recordVisit(sourceMemoryId, { reason: 'copy_source' }).catch(() => {
        // silent — visit recording is best-effort
      })

      setStatus('success')
      onCopy()
    } catch {
      setStatus('error')
    }
  }

  return (
    <Panel
      eyebrow="copy"
      title="Copy to user"
      subtitle="The scaffold keeps copy isolated so provenance, impersonation, and delete stay separate when the real admin flow lands."
    >
      <div className="detail-grid">
        <label className="field-stack">
          <span className="field-label">Source memory</span>
          <input className="control-input mono" value={sourceLabel} readOnly />
        </label>
        <label className="field-stack">
          <span className="field-label">Target scope</span>
          <select
            className="control-select"
            value={targetScope}
            onChange={(e) => {
              setTargetScope(e.target.value as ScopeKind)
              if (status === 'success' || status === 'error') setStatus('idle')
            }}
          >
            <option value="user">user</option>
            <option value="agent">agent</option>
            <option value="run">run</option>
          </select>
        </label>
      </div>

      <label className="field-stack">
        <span className="field-label">Target scope id</span>
        <input
          className="control-input mono"
          value={targetScopeId}
          onChange={(e) => {
            setTargetScopeId(e.target.value)
            if (status === 'success' || status === 'error') setStatus('idle')
          }}
          placeholder="e.g. bob"
        />
      </label>

      {status === 'success' && (
        <p className="surface-note">Memory copied successfully.</p>
      )}
      {status === 'error' && (
        <p className="surface-note is-error">Failed to copy memory.</p>
      )}
      {status === 'idle' && (
        <p className="surface-note">
          Writes from this shell add <span className="mono">impersonated_by=admin</span> and copy
          provenance via the admin API.
        </p>
      )}

      <div className="detail-actions">
        <button
          type="button"
          className="button"
          disabled={!canCopy}
          onClick={handleCopy}
        >
          {status === 'loading' ? 'Copying…' : 'Prepare copy'}
        </button>
        <button type="button" className="button-ghost" disabled>
          Preview provenance
        </button>
      </div>
    </Panel>
  )
}
