import { Outlet } from "react-router-dom"
import { Navbar } from "@/components/app/Navbar"

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t py-6 text-center text-xs text-muted-foreground">
        QuadroURL — link shortening service
      </footer>
    </div>
  )
}
