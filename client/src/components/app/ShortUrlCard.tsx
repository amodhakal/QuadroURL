import { Check, Copy, X } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { copyText } from "@/lib/api"
import { shortLink } from "@/api/urls"
import type { Url } from "@/types/api"

export function ShortUrlCard({
  url,
  onDismiss,
}: {
  url: Url
  onDismiss: () => void
}) {
  const [copied, setCopied] = useState(false)
  const link = shortLink(url.short_code)

  async function onCopy() {
    await copyText(link)
    setCopied(true)
    toast.success("Link copied to clipboard")
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Card className="mx-auto w-full max-w-xl border-primary/30">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Your short link is ready</CardTitle>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Dismiss"
            onClick={onDismiss}
          >
            <X />
          </Button>
        </div>
        <CardDescription>{url.title}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-2">
        <code className="flex-1 truncate rounded-lg border bg-muted px-3 py-2 font-mono text-sm">
          {link}
        </code>
        <Button onClick={onCopy} variant={copied ? "secondary" : "default"}>
          {copied ? <Check /> : <Copy />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </CardContent>
    </Card>
  )
}
