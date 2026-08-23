import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from '../components/app-shell.tsx'
import { MemoryDetailPage } from '../routes/memory-detail-page.tsx'
import { MemoriesPage } from '../routes/memories-page.tsx'
import { OverviewPage } from '../routes/overview-page.tsx'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <OverviewPage />,
      },
      {
        path: 'memories',
        element: <MemoriesPage />,
      },
      {
        path: 'memories/:memoryId',
        element: <MemoryDetailPage />,
      },
    ],
  },
])
