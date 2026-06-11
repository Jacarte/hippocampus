import type { ReactNode } from 'react'

type PanelProps = {
  title: string
  children: ReactNode
  eyebrow?: string
  subtitle?: string
  action?: ReactNode
  tone?: 'default' | 'accent'
}

export function Panel({
  title,
  children,
  eyebrow,
  subtitle,
  action,
  tone = 'default',
}: PanelProps) {
  return (
    <section className={`panel${tone === 'accent' ? ' panel--accent' : ''}`}>
      <div className="panel-header">
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h2 className="panel-title">{title}</h2>
          {subtitle ? <p className="panel-copy">{subtitle}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
