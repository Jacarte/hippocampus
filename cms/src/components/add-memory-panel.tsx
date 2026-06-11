import { useState } from 'react'
import { adminApi } from '../lib/api/admin.ts'
import type { ScopeKind } from '../lib/api/types.ts'
import { Panel } from './panel.tsx'

type AddMemoryPanelProps = {
  scope: ScopeKind
  scopeId: string
  onAddSuccess?: () => void
}

export function AddMemoryPanel({ scope, scopeId, onAddSuccess }: AddMemoryPanelProps) {
  const [content, setContent] = useState('')
  const [memoryType, setMemoryType] = useState('stable-fact')
  const [projectId, setProjectId] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')

  const canAdd = scopeId.length > 0 && content.trim().length > 0 && status !== 'loading'

  const handleAdd = async () => {
    if (!canAdd) return

    setStatus('loading')

    try {
      await adminApi.createMemory({
        scope,
        scope_id: scopeId,
        messages: [{ role: 'user', content: content.trim() }],
        metadata: { type: memoryType, project_id: projectId || undefined },
      })

      setContent('')
      setStatus('success')
      onAddSuccess?.()
    } catch {
      setStatus('error')
    }
  }

  return (
    <Panel eyebrow="create" title={`Add memory as — ${scope}:${scopeId}`}>
      <div className="add-row add-row--stack">
        <textarea
          className="control-textarea"
          value={content}
          onChange={(e) => {
            setContent(e.target.value)
            if (status === 'success' || status === 'error') setStatus('idle')
          }}
          placeholder="e.g. Prefers concise answers; uses TanStack Router."
        />
        <div className="add-row-footer">
          <input
            type="text"
            className="control-input mono"
            value={projectId}
            onChange={(e) => {
              setProjectId(e.target.value)
              if (status === 'success' || status === 'error') setStatus('idle')
            }}
            placeholder="e.g. my-project"
          />
          <select
            className="control-select"
            value={memoryType}
            onChange={(e) => setMemoryType(e.target.value)}
          >
            <option value="decision">decision</option>
            <option value="stable-fact">stable-fact</option>
            <option value="procedure">procedure</option>
            <option value="problem-fix">problem-fix</option>
          </select>
          <button type="button" className="button" disabled={!canAdd} onClick={handleAdd}>
            {status === 'loading' ? 'Adding…' : 'Add'}
          </button>
        </div>
      </div>

      {status === 'success' && (
        <p className="surface-note">Memory created successfully.</p>
      )}
      {status === 'error' && (
        <p className="surface-note is-error">Failed to create memory.</p>
      )}
      {status === 'idle' && scopeId.length === 0 && (
        <p className="surface-note">Set an impersonation user above to add memories.</p>
      )}
    </Panel>
  )
}
