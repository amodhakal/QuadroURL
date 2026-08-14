import { useEffect, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { Loader2 } from "lucide-react"

import { resolveShortCode } from "@/api/urls"
import { Button } from "@/components/ui/button"

export function RedirectPage() {
  const { code } = useParams<{ code: string }>()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!code) return

    let cancelled = false
    resolveShortCode(code)
      .then((data) => {
        if (!cancelled) window.location.replace(data.url)
      })
      .catch(() => {
        if (!cancelled) {
          setError("This short link doesn't exist or is no longer active.")
        }
      })

    return () => {
      cancelled = true
    }
  }, [code])

  if (error) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <div className="space-y-3 text-center">
          <p className="text-muted-foreground">{error}</p>
          <Button asChild variant="outline">
            <Link to="/">Back home</Link>
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="grid min-h-[60vh] place-items-center">
      <div className="flex items-center gap-3 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
        <span>Redirecting you…</span>
      </div>
    </div>
  )
}
