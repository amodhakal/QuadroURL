import { AskPanel } from "@/components/app/AskPanel"
import { SearchPanel } from "@/components/app/SearchPanel"

export function Search() {
  return (
    <div className="mx-auto max-w-6xl space-y-10 px-4 py-10 sm:px-6">
      <section className="space-y-3 text-center">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Search your links
        </h1>
        <p className="mx-auto max-w-md text-muted-foreground">
          Semantic search finds links by meaning, and Q&amp;A synthesizes an
          answer from the most relevant ones.
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <SearchPanel />
        <AskPanel />
      </div>
    </div>
  )
}
