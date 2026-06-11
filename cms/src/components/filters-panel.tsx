import { useEffect, useRef, useState } from 'react'
import { Panel } from './panel.tsx'
import { adminApi } from '../lib/api/admin.ts'
import type { ScopeKind } from '../lib/api/types.ts'

const SCOPE_KEYS = new Set(['user', 'agent', 'run'])

const FILTER_FIELDS = [
  { label: 'User', key: 'user' as const },
  { label: 'Agent', key: 'agent' as const },
  { label: 'Project', key: 'project' as const },
  { label: 'Run', key: 'run' as const },
] as const

type FiltersPanelProps = {
  onScopeChange: (scope: ScopeKind, scopeId: string) => void
  onQueryChange?: (query: string) => void
}

export function FiltersPanel({ onScopeChange, onQueryChange }: FiltersPanelProps) {
  const [filters, setFilters] = useState<Record<string, string>>({
    user: '',
    agent: '',
    project: '',
    run: '',
  })
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({
    user: [],
    agent: [],
    project: [],
    run: [],
  })

  useEffect(() => {
    adminApi.listScopes().then((scopes) => {
      setSuggestions({
        user: scopes.users,
        agent: scopes.agents,
        project: scopes.projects,
        run: scopes.runs,
      })
    }).catch(() => {
      // Scopes endpoint may fail; silently keep empty suggestions
    })
  }, [])

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleChange = (key: string, value: string) => {
    if (SCOPE_KEYS.has(key)) {
      // Scope fields: set the active scope and clear other scope fields
      setFilters({ user: '', agent: '', project: filters.project, run: '', [key]: value })
    } else {
      // Project field: text-only query
      setFilters((prev) => ({ ...prev, [key]: value }))
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      if (SCOPE_KEYS.has(key)) {
        onScopeChange(key as ScopeKind, value)
      } else {
        onQueryChange?.(value)
      }
    }, 300)
  }

  const handleClear = () => {
    const cleared = { user: '', agent: '', project: '', run: '' }
    setFilters(cleared)
    onScopeChange('user', '')
    onQueryChange?.('')
  }

  return (
    <Panel
      eyebrow="filters"
      title="Filter memories"
      action={
        <button type="button" className="button-text" onClick={handleClear}>
          Clear
        </button>
      }
    >
      <div className="filters-grid">
        {FILTER_FIELDS.map((field) => (
          <label key={field.label} className="field-stack">
            <span className="field-label">{field.label}</span>
            <input
              type="text"
              className="control-input"
              placeholder="e.g. alice"
              value={filters[field.key]}
              onChange={(e) => handleChange(field.key, e.target.value)}
              list={`filter-suggestions-${field.key}`}
            />
            <datalist id={`filter-suggestions-${field.key}`}>
              {suggestions[field.key].map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </label>
        ))}
      </div>
    </Panel>
  )
}
