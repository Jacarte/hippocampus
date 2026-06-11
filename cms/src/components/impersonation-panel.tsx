import { useState } from 'react'
import {
  getImpersonationFields,
  totalReferenceMemoryCount,
  type ImpersonationSelection,
} from '../lib/mock-data.ts'
import type { ScopeKind } from '../lib/api/types.ts'

type ImpersonationPanelProps = {
  selection: ImpersonationSelection
  onImpersonate: (scope: ScopeKind, scopeId: string) => void
}

export function ImpersonationPanel({ selection, onImpersonate }: ImpersonationPanelProps) {
  const [fields, setFields] = useState(() => getImpersonationFields(selection))

  const handleChange = (index: number, newValue: string) => {
    const updated = [...fields]
    updated[index] = { ...updated[index], value: newValue }
    setFields(updated)
  }

  const handleSet = () => {
    const userField = fields[0]
    onImpersonate('user', userField.value)
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

        <span className="count-badge">{totalReferenceMemoryCount} memories</span>
      </div>

      <div className="field-grid">
        {fields.map((field, i) => (
          <label key={field.label} className="field-stack">
            <span className="field-label">{field.label}</span>
            <select
              className="control-select"
              value={field.value}
              onChange={(e) => handleChange(i, e.target.value)}
            >
              {field.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        ))}

        <button type="button" className="button" onClick={handleSet}>
          Set impersonation
        </button>
      </div>
    </section>
  )
}
