import { RouterProvider } from 'react-router-dom'
import { router } from './app/router.tsx'
import { ScopeProvider } from './lib/scope-context.tsx'

function App() {
  return (
    <ScopeProvider>
      <RouterProvider router={router} />
    </ScopeProvider>
  )
}

export default App
