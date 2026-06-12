import { AddMemoryPanel } from './add-memory-panel.tsx'
import { FiltersPanel } from './filters-panel.tsx'
import { ImpersonationPanel } from './impersonation-panel.tsx'
import { MemoryListShell } from './memory-list-shell.tsx'
import { useScopeContext } from '../lib/scope-context.tsx'
import type { ScopeKind } from '../lib/api/types.ts'

export function OperatorDashboard() {
  const { scope, setScope, scopeId, setScopeId, query, setQuery, typeFilter, setTypeFilter, projectFilter, setProjectFilter, refreshKey, triggerRefresh, totalMemoryCount, setTotalMemoryCount } = useScopeContext()

  const handleImpersonate = (newScope: ScopeKind, newScopeId: string) => {
    setScope(newScope)
    setScopeId(newScopeId)
  }

  return (
    <div className="page-stack">
      <ImpersonationPanel onImpersonate={handleImpersonate} totalMemoryCount={totalMemoryCount} />
      <FiltersPanel onScopeChange={handleImpersonate} onQueryChange={setQuery} onTypeChange={setTypeFilter} onProjectChange={setProjectFilter} />
      <AddMemoryPanel scope={scope} scopeId={scopeId} onAddSuccess={triggerRefresh} />
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
