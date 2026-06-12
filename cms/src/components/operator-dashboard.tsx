import { useState } from 'react'
import { AddMemoryPanel } from './add-memory-panel.tsx'
import { FiltersPanel } from './filters-panel.tsx'
import { ImpersonationPanel } from './impersonation-panel.tsx'
import { MemoryListShell } from './memory-list-shell.tsx'
import { useScopeContext } from '../lib/scope-context.tsx'
import { adminApi } from '../lib/api/admin.ts'
import type { ScopeKind } from '../lib/api/types.ts'

export function OperatorDashboard() {
  const { scope, setScope, scopeId, setScopeId, query, setQuery, typeFilter, setTypeFilter, projectFilter, setProjectFilter, refreshKey, triggerRefresh, totalMemoryCount, setTotalMemoryCount } = useScopeContext()
  const [deletingEmpty, setDeletingEmpty] = useState(false)
  const [emptyDeleteMessage, setEmptyDeleteMessage] = useState('')

  const handleImpersonate = (newScope: ScopeKind, newScopeId: string) => {
    setScope(newScope)
    setScopeId(newScopeId)
  }

  const handleDeleteEmpty = async () => {
    setDeletingEmpty(true)
    setEmptyDeleteMessage('')
    try {
      const result = await adminApi.deleteEmptyMemories()
      setEmptyDeleteMessage(result.message)
      triggerRefresh()
    } catch (err) {
      setEmptyDeleteMessage(err instanceof Error ? err.message : 'Failed to delete empty memories')
    }
    setDeletingEmpty(false)
  }

  return (
    <div className="page-stack">
      <ImpersonationPanel onImpersonate={handleImpersonate} totalMemoryCount={totalMemoryCount} />
      <FiltersPanel onScopeChange={handleImpersonate} onQueryChange={setQuery} onTypeChange={setTypeFilter} onProjectChange={setProjectFilter} type={typeFilter} project={projectFilter} />
      <AddMemoryPanel scope={scope} scopeId={scopeId} onAddSuccess={triggerRefresh} />
      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="button-text is-danger"
          disabled={deletingEmpty}
          onClick={handleDeleteEmpty}
        >
          {deletingEmpty ? 'Deleting…' : 'Delete empty'}
        </button>
        {emptyDeleteMessage && <span className="micro-label">{emptyDeleteMessage}</span>}
      </div>
      <MemoryListShell
        scope={scope}
        scopeId={scopeId}
        query={query}
        type={typeFilter}
        project={projectFilter}
        refreshKey={refreshKey}
        onTotalCount={setTotalMemoryCount}
        onDeleteSuccess={triggerRefresh}
      />
    </div>
  )
}
