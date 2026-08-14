import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"

export function NotFound() {
  return (
    <div className="grid min-h-[60vh] place-items-center">
      <div className="space-y-4 text-center">
        <p className="font-mono text-sm text-muted-foreground">404</p>
        <h1 className="text-2xl font-semibold tracking-tight">
          Page not found
        </h1>
        <Button asChild>
          <Link to="/">Back home</Link>
        </Button>
      </div>
    </div>
  )
}
