import { ArrowUpRight, Globe2 } from "lucide-react"

import { parseDestination } from "@/lib/link-tools"

export function LinkPreview({ url, title }: { url: string; title?: string }) {
  const destination = parseDestination(url)

  return (
    <section aria-label="Destination preview" className="min-w-0 overflow-hidden rounded-xl border border-primary/25 bg-muted/30">
      <div className="flex items-center gap-2 border-b px-4 py-2 text-xs font-medium uppercase tracking-widest text-muted-foreground">
        <Globe2 className="size-3.5" aria-hidden="true" /> Destination preview
      </div>
      <div className="space-y-2 p-4">
        {destination ? (
          <>
            <p className="break-all font-mono text-xs text-muted-foreground">{destination.host}</p>
            <p className="break-words font-serif text-xl">{title?.trim() || destination.hostname}</p>
            <p className="break-all font-mono text-xs leading-relaxed text-muted-foreground">{url}</p>
            <a href={destination.href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-sm underline underline-offset-4">
              Open destination <ArrowUpRight className="size-4" aria-hidden="true" />
            </a>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Enter an HTTP or HTTPS URL without embedded credentials to preview it.</p>
        )}
        <p className="pt-2 text-xs text-muted-foreground">Local preview only. No page content or images are fetched.</p>
      </div>
    </section>
  )
}
