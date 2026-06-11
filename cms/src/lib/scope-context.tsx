import { createContext, useContext, useState, type ReactNode } from 'react'
import type { ScopeKind } from './api/types.ts'

type ScopeContextValue = {
  scope: ScopeKind
  setScope: (s: ScopeKind) => void
  scopeId: string
  setScopeId: (id: string) => void
  query: string
  setQuery: (q: string) => void
  refreshKey: number
  triggerRefresh: () => void
  totalMemoryCount: number | undefined
  setTotalMemoryCount: (c: number | undefined) => void
}

const ScopeContext = createContext<ScopeContextValue | null>(null)

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScope] = useState<ScopeKind>('user')
  const [scopeId, setScopeId] = useState('')
  const [query, setQuery] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [totalMemoryCount, setTotalMemoryCount] = useState<number>()

  const triggerRefresh = () => setRefreshKey((k) => k + 1)

  return (
    <ScopeContext.Provider
      value={{
        scope,
        setScope,
        scopeId,
        setScopeId,
        query,
        setQuery,
        refreshKey,
        triggerRefresh,
        totalMemoryCount,
        setTotalMemoryCount,
      }}
    >
      {children}
    </ScopeContext.Provider>
  )
}

export function useScopeContext(): ScopeContextValue {
  const ctx = useContext(ScopeContext)
  if (!ctx) throw new Error('useScopeContext must be used within ScopeProvider')
  return ctx
}
