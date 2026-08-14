import { createBrowserRouter } from "react-router-dom"

import { AppLayout } from "@/components/app/AppLayout"
import { Home } from "@/pages/Home"
import { NotFound } from "@/pages/NotFound"
import { RedirectPage } from "@/pages/RedirectPage"
import { Users } from "@/pages/Users"

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <Home /> },
      { path: "users", element: <Users /> },
      { path: "r/:code", element: <RedirectPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
])
