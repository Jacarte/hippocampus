import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { adminApi } from '../lib/api/admin.ts'
import { getBackendDisplayUrl } from '../lib/config.ts'

type BackendState = 'loading' | 'ready' | 'offline'

const navigation = [
  { to: '/', label: 'Overview' },
  { to: '/memories', label: 'Memories' },
]

export function AppShell() {
  const [backendState, setBackendState] = useState<BackendState>('loading')

  useEffect(() => {
    let active = true

    void adminApi
      .getHealth()
      .then(() => {
        if (active) {
          setBackendState('ready')
        }
      })
      .catch(() => {
        if (active) {
          setBackendState('offline')
        }
      })

    return () => {
      active = false
    }
  }, [])

  return (
    <div className="shell-frame">
      <header className="shell-topbar">
        <div className="backend-pill" aria-live="polite">
          <span className="backend-dot" data-state={backendState} aria-hidden="true" />
          <span>
            {backendState === 'ready' ? 'Backend ready' : null}
            {backendState === 'loading' ? 'Checking backend' : null}
            {backendState === 'offline' ? 'Backend unavailable' : null}
          </span>
          <span className="mono">{getBackendDisplayUrl()}</span>
        </div>

        <nav className="route-switcher" aria-label="Primary navigation">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `route-link${isActive ? ' is-active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="content-shell">
        <Outlet />
      </main>
    </div>
  )
}
