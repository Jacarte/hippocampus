import { useEffect, useRef, useState } from 'react'
import { Panel } from './panel.tsx'
import { adminApi } from '../lib/api/admin.ts'
import type { ScopeKind } from '../lib/api/types.ts'

const MEMORY_TYPES = ['', 'stable-fact', 'decision', 'procedure', 'problem-fix'] as const

type FiltersPanelProps = {
  onScopeChange: (scope: ScopeKind, scopeId: string) => void
  onQueryChange: (query: string) => void
  onTypeChange: (type: string) => void
  onProjectChange: (project: string) => void
}

export function FiltersPanel({ onScopeChange, onQueryChange, onTypeChange, onProjectChange }: FiltersPanelProps) {
  const [search, setSearch] = useState('')
  const [typeValue, setTypeValue] = useState('')
  const [scopeFilters, setScopeFilters] = useState<Record<string, string>>({
    user: '',
    agent: '',
    run: '',
  })
  const [project, setProject] = useState('')
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({
    user: [],
    agent: [],
    run: [],
  })

  useEffect(() => {
    adminApi.listScopes().then((scopes) => {
      setSuggestions({
        user: scopes.users,
        agent: scopes.agents,
        run: scopes.runs,
      })
    }).catch(() => {
      // Scopes endpoint may fail; silently keep empty suggestions
    })
  }, [])

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const debouncedCallback = (fn: () => void) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(fn, 300)
  }

  const handleSearchChange = (value: string) => {
    setSearch(value)
    debouncedCallback(() => onQueryChange(value))
  }

  const handleTypeChange = (value: string) => {
    setTypeValue(value)
    onTypeChange(value)
  }

  const handleScopeChange = (key: string, value: string) => {
    setScopeFilters({ user: '', agent: '', run: '', [key]: value })
    debouncedCallback(() => onScopeChange(key as ScopeKind, value))
  }

  const handleProjectChange = (value: string) => {
    setProject(value)
    debouncedCallback(() => onProjectChange(value))
  }

  const handleClear = () => {
    setSearch('')
    setTypeValue('')
    setScopeFilters({ user: '', agent: '', run: '' })
    setProject('')
    onQueryChange('')
    onTypeChange('')
    onScopeChange('user', '')
    onProjectChange('')
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
      <div className="field-stack">
        <span className="field-label">Search</span>
        <input
          type="text"
          className="control-input"
          placeholder="Search memories…"
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
        />
      </div>
      <div className="filters-grid">
        <label className="field-stack">
          <span className="field-label">Type</span>
          <select
            className="control-select"
            value={typeValue}
            onChange={(e) => handleTypeChange(e.target.value)}
          >
            {MEMORY_TYPES.map((t) => (
              <option key={t} value={t}>{t || 'All'}</option>
            ))}
          </select>
        </label>
        {(['user', 'agent', 'run'] as const).map((key) => (
          <label key={key} className="field-stack">
            <span className="field-label">{key.charAt(0).toUpperCase() + key.slice(1)}</span>
            <input
              type="text"
              className="control-input"
              placeholder="e.g. alice"
              value={scopeFilters[key]}
              onChange={(e) => handleScopeChange(key, e.target.value)}
              list={`filter-suggestions-${key}`}
            />
            <datalist id={`filter-suggestions-${key}`}>
              {suggestions[key].map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </label>
        ))}
        <label className="field-stack">
          <span className="field-label">Project</span>
          <input
            type="text"
            className="control-input"
            placeholder="e.g. my-project"
            value={project}
            onChange={(e) => handleProjectChange(e.target.value)}
          />
        </label>
      </div>
    </Panel>
  )
}
