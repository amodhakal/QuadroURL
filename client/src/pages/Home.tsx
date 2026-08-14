import { useState } from "react"

import { ShortUrlCard } from "@/components/app/ShortUrlCard"
import { UrlForm } from "@/components/app/UrlForm"
import { UrlTable } from "@/components/app/UrlTable"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { Url } from "@/types/api"

export function Home() {
  const [created, setCreated] = useState<Url | null>(null)

  return (
    <div className="mx-auto max-w-6xl space-y-10 px-4 py-10 sm:px-6">
      <section className="space-y-3 text-center">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Shorten a link
        </h1>
        <p className="mx-auto max-w-md text-muted-foreground">
          Turn long, unwieldy URLs into clean, shareable short links with a
          glanceable code.
        </p>
      </section>

      <Card className="mx-auto w-full max-w-xl">
        <CardHeader>
          <CardTitle className="text-base">New short link</CardTitle>
        </CardHeader>
        <CardContent>
          <UrlForm onCreated={(url) => setCreated(url)} />
        </CardContent>
      </Card>

      {created && (
        <ShortUrlCard url={created} onDismiss={() => setCreated(null)} />
      )}

      <UrlTable />
    </div>
  )
}
