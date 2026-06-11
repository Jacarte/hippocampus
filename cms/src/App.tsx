import { RouterProvider } from 'react-router-dom'
import { router } from './app/router.tsx'

function App() {
  return <RouterProvider router={router} />
}

export default App
import { createBrowserRouter, RouterProvider } from "react-router-dom";

const router = createBrowserRouter([
  {
    path: "/",
    element: <div>Mem0 CMS</div>,
  },
  {
    path: "/memories",
    element: <div>Memories</div>,
  },
  {
    path: "/memories/:id",
    element: <div>Memory Detail</div>,
  },
  {
    path: "/index",
    element: <div>Index</div>,
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
