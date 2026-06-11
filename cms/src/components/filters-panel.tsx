import { filterFields } from '../lib/mock-data.ts'
import { Panel } from './panel.tsx'

export function FiltersPanel() {
  return (
    <Panel
      eyebrow="filters"
      title="Filter memories"
      action={
        <button type="button" className="button-text">
          Clear
        </button>
      }
    >
      <div className="filters-grid">
        {filterFields.map((field) => (
          <label key={field.label} className="field-stack">
            <span className="field-label">{field.label}</span>
            <select className="control-select" defaultValue={field.value}>
              {field.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </Panel>
  )
}
