import { zodResolver } from "@hookform/resolvers/zod"
import { MessageCircleQuestion } from "lucide-react"
import { useForm } from "react-hook-form"
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
import { useAskQuestion } from "@/hooks/use-search"
import { ApiError } from "@/lib/api"
import { parseDestination } from "@/lib/link-tools"

const formSchema = z.object({
  question: z
    .string()
    .trim()
    .min(1, "Enter a question")
    .max(2000, "Question is too long"),
})

type FormValues = z.infer<typeof formSchema>

export function AskPanel() {
  const ask = useAskQuestion()

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { question: "" },
  })

  function onSubmit(values: FormValues) {
    ask.mutate({ question: values.question })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ask your links</CardTitle>
        <CardDescription>
          A RAG answer synthesized from your most relevant saved links, with cited sources.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-2">
            <FormField
              control={form.control}
              name="question"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormControl>
                    <Input
                      placeholder="e.g. Which saved link covers Postgres pooling?"
                      aria-label="Question"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={ask.isPending}>
              {ask.isPending ? null : <MessageCircleQuestion />}
              {ask.isPending ? "Asking…" : "Ask"}
            </Button>
          </form>
        </Form>

        {ask.isPending ? (
          <div className="space-y-3" aria-label="Loading answer">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : ask.isError ? (
          <p className="text-sm text-destructive" role="alert">
            {ask.error instanceof ApiError
              ? ask.error.message
              : "Could not answer. Is the API running?"}
          </p>
        ) : ask.data ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Badge variant="outline">{ask.data.model}</Badge>
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{ask.data.answer}</p>
            {ask.data.sources.length > 0 && (
              <ol className="space-y-2 border-t pt-3" aria-label="Sources">
                {ask.data.sources.map((source, index) => (
                  <li key={source.id} className="flex items-baseline gap-2 text-sm">
                    <span className="font-mono text-xs text-muted-foreground">[{index + 1}]</span>
                    <div className="min-w-0">
                      <span className="font-mono text-xs">/{source.short_code}</span>
                      <span className="text-muted-foreground"> — {source.title} </span>
                      <a
                        href={parseDestination(source.original_url)?.href ?? shortLink(source.short_code)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs underline underline-offset-4"
                      >
                        Open
                      </a>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            The answer will appear here with its sources.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
