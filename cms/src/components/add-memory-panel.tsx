import { Panel } from './panel.tsx'

export function AddMemoryPanel() {
  return (
    <Panel eyebrow="create" title="Add memory as —">
      <div className="add-row">
        <textarea
          className="control-textarea"
          defaultValue=""
          placeholder="e.g. Prefers concise answers; uses TanStack Router."
        />
        <button type="button" className="button" disabled>
          Add
        </button>
      </div>

      <p className="surface-note">Set an impersonation user above to add memories.</p>
    </Panel>
  )
}
