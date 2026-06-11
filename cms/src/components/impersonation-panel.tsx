import { useEffect, useRef, useState } from 'react'
import type { ScopeKind } from '../lib/api/types.ts'
import { adminApi } from '../lib/api/admin.ts'

type ImpersonationPanelProps = {
  onImpersonate: (scope: ScopeKind, scopeId: string) => void
  totalMemoryCount?: number
}

const IMPERSONATION_FIELDS = [
  { label: 'Impersonate · User', key: 'user' as const },
  { label: 'Agent', key: 'agent' as const },
  { label: 'Project', key: 'project' as const },
  { label: 'Run', key: 'run' as const },
] as const

export function ImpersonationPanel({ onImpersonate, totalMemoryCount }: ImpersonationPanelProps) {
  const [fields, setFields] = useState<Record<string, string>>({
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
  const fieldsRef = useRef(fields)
  fieldsRef.current = fields

  const handleChange = (key: string, value: string) => {
    setFields((prev) => ({ ...prev, [key]: value }))

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const current = fieldsRef.current
      const scopeFields: [ScopeKind, string][] = [
        ['user', current.user],
        ['agent', current.agent],
        ['run', current.run],
      ]
      for (const [scope, id] of scopeFields) {
        if (id) {
          onImpersonate(scope, id)
          return
        }
      }
      onImpersonate('user', current.user)
    }, 300)
  }

  const handleSet = () => {
    if (debounceRef.current) clearTimeout(debounceRef.current)

    const scopeFields: [ScopeKind, string][] = [
      ['user', fields.user],
      ['agent', fields.agent],
      ['run', fields.run],
    ]
    for (const [scope, id] of scopeFields) {
      if (id) {
        onImpersonate(scope, id)
        return
      }
    }
    // Fallback: impersonate user even with empty id
    onImpersonate('user', fields.user)
  }

  return (
    <section className="hero-panel" aria-label="Impersonation controls">
      <div className="hero-header">
        <div className="section-stack">
          <h1 className="hero-title">mem0.cms</h1>
          <p className="hero-copy">
            Impersonate, browse, edit, copy across users, and simulate decay.
          </p>
        </div>

        <span className="count-badge">{totalMemoryCount ?? '—'} memories</span>
      </div>

      <div className="field-grid">
        {IMPERSONATION_FIELDS.map((field) => (
          <label key={field.label} className="field-stack">
            <span className="field-label">{field.label}</span>
            <input
              type="text"
              className="control-input"
              placeholder="e.g. alice"
              value={fields[field.key]}
              onChange={(e) => handleChange(field.key, e.target.value)}
              list={`impersonation-suggestions-${field.key}`}
            />
            <datalist id={`impersonation-suggestions-${field.key}`}>
              {suggestions[field.key].map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </label>
        ))}

        <button type="button" className="button" onClick={handleSet}>
          Set impersonation
        </button>
      </div>
    </section>
  )
}
