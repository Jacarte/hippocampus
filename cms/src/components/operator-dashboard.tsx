import { useState } from 'react'
import { activeImpersonation } from '../lib/mock-data.ts'
import { AddMemoryPanel } from './add-memory-panel.tsx'
import { FiltersPanel } from './filters-panel.tsx'
import { ImpersonationPanel } from './impersonation-panel.tsx'
import { MemoryListShell } from './memory-list-shell.tsx'
import type { ScopeKind } from '../lib/api/types.ts'

export function OperatorDashboard() {
  const [scope, setScope] = useState<ScopeKind>('user')
  const [scopeId, setScopeId] = useState('alice')

  const handleImpersonate = (newScope: ScopeKind, newScopeId: string) => {
    setScope(newScope)
    setScopeId(newScopeId)
  }

  return (
    <div className="page-stack">
      <ImpersonationPanel selection={activeImpersonation} onImpersonate={handleImpersonate} />
      <FiltersPanel />
      <AddMemoryPanel />
      <MemoryListShell scope={scope} scopeId={scopeId} />
    </div>
  )
}
