import { zodResolver } from "@hookform/resolvers/zod"
import { Copy, ExternalLink, Search } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { shortLink } from "@/api/urls"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useSemanticSearch } from "@/hooks/use-search"
import { copyText, ApiError } from "@/lib/api"
import { parseDestination } from "@/lib/link-tools"
import type { SearchHit } from "@/types/api"

const formSchema = z.object({
  query: z.string().trim().min(1, "Enter a search query").max(500, "Query is too long"),
})

type FormValues = z.infer<typeof formSchema>

function scorePercent(score: number): number {
  return Math.max(0, Math.round(score * 100))
}

function SearchHitRow({ hit }: { hit: SearchHit }) {
  function onCopy() {
    copyText(shortLink(hit.short_code))
    toast.success("Link copied")
  }

  return (
    <li className="flex items-start justify-between gap-3 border-b py-3 last:border-0">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">/{hit.short_code}</span>
          <Badge variant="secondary" aria-label={`Relevance ${scorePercent(hit.score)} percent`}>
            {scorePercent(hit.score)}%
          </Badge>
        </div>
        <p className="truncate text-sm">{hit.title}</p>
        <a
          href={parseDestination(hit.original_url)?.href}
          target="_blank"
          rel="noreferrer"
          className="block truncate text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {hit.original_url}
        </a>
      </div>
      <div className="flex shrink-0 gap-1">
        <Button variant="ghost" size="icon-sm" onClick={onCopy} aria-label={`Copy ${hit.short_code}`}>
          <Copy />
        </Button>
        <Button variant="ghost" size="icon-sm" asChild>
          <a
            href={shortLink(hit.short_code)}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${hit.short_code}`}
          >
            <ExternalLink />
          </a>
        </Button>
      </div>
    </li>
  )
}

export function SearchPanel() {
  const [submitted, setSubmitted] = useState("")
  const { data, isLoading, isError, error } = useSemanticSearch(submitted)

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { query: "" },
  })

  function onSubmit(values: FormValues) {
    setSubmitted(values.query)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Semantic search</CardTitle>
        <CardDescription>
          Find saved links by meaning, not keywords. Results are ranked by vector similarity.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-2">
            <FormField
              control={form.control}
              name="query"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormControl>
                    <Input
                      placeholder="e.g. postgres connection pooling"
                      aria-label="Search query"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={isLoading}>
              {isLoading ? null : <Search />}
              {isLoading ? "Searching…" : "Search"}
            </Button>
          </form>
        </Form>

        {isLoading ? (
          <div className="space-y-3" aria-label="Loading results">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : isError ? (
          <p className="text-sm text-destructive" role="alert">
            {error instanceof ApiError ? error.message : "Search failed. Is the API running?"}
          </p>
        ) : data ? (
          data.results.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No saved links match “{data.query}”.
            </p>
          ) : (
            <ul aria-label="Search results">
              {data.results.map((hit) => (
                <SearchHitRow key={hit.id} hit={hit} />
              ))}
            </ul>
          )
        ) : (
          <p className="text-sm text-muted-foreground">
            Results will appear here.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
