import "@testing-library/jest-dom/vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { askQuestion, searchLinks } from "@/api/search"
import type { AskResponse, SearchHit, SearchResponse } from "@/types/api"
import { Search } from "./Search"

vi.mock("@/api/search", () => ({
  searchLinks: vi.fn(),
  askQuestion: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const hit: SearchHit = {
  id: 1,
  user_id: 7,
  short_code: "pool01",
  original_url: "https://example.com/pooling",
  title: "Pooling guide",
  score: 0.8731,
}
const searchResponse: SearchResponse = { kind: "search", query: "pooling", results: [hit] }
const askResponse: AskResponse = {
  answer: "Pooling is covered by [1].",
  model: "test-model",
  sources: [hit],
}

function renderSearch() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}><Search /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(searchLinks).mockReset().mockResolvedValue({ kind: "search", query: "", results: [] })
  vi.mocked(askQuestion).mockReset()
})

describe("Search", () => {
  it("renders both panels with empty states", () => {
    renderSearch()
    expect(screen.getByRole("heading", { name: "Search your links" })).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Search query" })).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Question" })).toBeInTheDocument()
    expect(screen.getByText("Results will appear here.")).toBeInTheDocument()
    expect(screen.getByText("The answer will appear here with its sources.")).toBeInTheDocument()
  })

  it("validates empty input without fetching", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.click(screen.getByRole("button", { name: "Search" }))
    expect(await screen.findByText("Enter a search query")).toBeInTheDocument()
    expect(searchLinks).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByText("Enter a question")).toBeInTheDocument()
    expect(askQuestion).not.toHaveBeenCalled()
  })

  it("searches and renders ranked hits with scores", async () => {
    const user = userEvent.setup()
    vi.mocked(searchLinks).mockResolvedValue(searchResponse)
    renderSearch()

    await user.type(screen.getByRole("textbox", { name: "Search query" }), "pooling")
    await user.click(screen.getByRole("button", { name: "Search" }))

    expect(await screen.findByText("/pool01")).toBeInTheDocument()
    expect(screen.getByText("Pooling guide")).toBeInTheDocument()
    expect(screen.getByLabelText("Relevance 87 percent")).toBeInTheDocument()
    expect(searchLinks).toHaveBeenCalledExactlyOnceWith("pooling", 5)
  })

  it("asks and renders the answer with cited sources", async () => {
    const user = userEvent.setup()
    vi.mocked(askQuestion).mockResolvedValue(askResponse)
    renderSearch()

    await user.type(screen.getByRole("textbox", { name: "Question" }), "pooling?")
    await user.click(screen.getByRole("button", { name: "Ask" }))

    expect(await screen.findByText("Pooling is covered by [1].")).toBeInTheDocument()
    expect(screen.getByText("test-model")).toBeInTheDocument()
    expect(screen.getByText("/pool01")).toBeInTheDocument()
    expect(askQuestion).toHaveBeenCalledExactlyOnceWith("pooling?", 5)
  })

  it("surfaces API errors inline", async () => {
    const user = userEvent.setup()
    const { ApiError } = await import("@/lib/api")
    vi.mocked(searchLinks).mockRejectedValue(new ApiError(503, "Semantic search unavailable"))
    renderSearch()

    await user.type(screen.getByRole("textbox", { name: "Search query" }), "pooling")
    await user.click(screen.getByRole("button", { name: "Search" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Semantic search unavailable")
    await waitFor(() => expect(screen.getByRole("button", { name: "Search" })).toBeEnabled())
  })
})
