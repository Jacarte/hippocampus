import { Panel } from './panel.tsx'

type CopyActionShellProps = {
  sourceLabel: string
}

export function CopyActionShell({ sourceLabel }: CopyActionShellProps) {
  return (
    <Panel
      eyebrow="copy"
      title="Copy to user"
      subtitle="The scaffold keeps copy isolated so provenance, impersonation, and delete stay separate when the real admin flow lands."
    >
      <div className="detail-grid">
        <label className="field-stack">
          <span className="field-label">Source memory</span>
          <input className="control-input mono" defaultValue={sourceLabel} />
        </label>
        <label className="field-stack">
          <span className="field-label">Target scope</span>
          <select className="control-select" defaultValue="user">
            <option value="user">user</option>
            <option value="agent">agent</option>
            <option value="run">run</option>
          </select>
        </label>
      </div>

      <label className="field-stack">
        <span className="field-label">Target scope id</span>
        <input className="control-input mono" defaultValue="target-user" />
      </label>

      <p className="surface-note">
        Writes from this shell are expected to add <span className="mono">impersonated_by=admin</span>{' '}
        and copy provenance once the backend flow is wired.
      </p>

      <div className="detail-actions">
        <button type="button" className="button">
          Prepare copy
        </button>
        <button type="button" className="button-ghost">
          Preview provenance
        </button>
      </div>
    </Panel>
  )
}
