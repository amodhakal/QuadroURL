import type { ReactNode } from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { askQuestion, searchLinks } from "@/api/search"
import type { AskResponse, SearchResponse } from "@/types/api"
import { useAskQuestion, useSemanticSearch } from "./use-search"

vi.mock("@/api/search", () => ({
  searchLinks: vi.fn(),
  askQuestion: vi.fn(),
}))

const response: SearchResponse = {
  kind: "search",
  query: "pooling",
  results: [
    {
      id: 1,
      user_id: 7,
      short_code: "pool01",
      original_url: "https://example.com/pooling",
      title: "Pooling guide",
      score: 0.9,
    },
  ],
}
const answer: AskResponse = {
  answer: "Pooling is covered by [1].",
  model: "test-model",
  sources: response.results,
}
let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.resetAllMocks()
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
})

afterEach(() => {
  queryClient.clear()
})

describe("useSemanticSearch", () => {
  it("queries once submitted text is provided", async () => {
    vi.mocked(searchLinks).mockResolvedValue(response)
    const { result } = renderHook(() => useSemanticSearch("pooling"), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(searchLinks).toHaveBeenCalledExactlyOnceWith("pooling", 5)
    expect(result.current.data).toEqual(response)
  })

  it("stays idle for blank queries without fetching", async () => {
    const { result } = renderHook(() => useSemanticSearch("   "), { wrapper })

    expect(result.current.fetchStatus).toBe("idle")
    expect(searchLinks).not.toHaveBeenCalled()
  })

  it("exposes a query failure without retrying", async () => {
    const error = new Error("Unavailable")
    vi.mocked(searchLinks).mockRejectedValue(error)
    const { result } = renderHook(() => useSemanticSearch("pooling"), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error).toBe(error)
    expect(searchLinks).toHaveBeenCalledExactlyOnceWith("pooling", 5)
  })
})

describe("useAskQuestion", () => {
  it.each([true, false])("forwards the question (success=%s)", async (succeeded) => {
    const error = new Error("Unavailable")
    if (succeeded) vi.mocked(askQuestion).mockResolvedValue(answer)
    else vi.mocked(askQuestion).mockRejectedValue(error)
    const { result } = renderHook(() => useAskQuestion(), { wrapper })

    await act(async () => {
      const mutation = result.current.mutateAsync({ question: "pooling?" })
      if (succeeded) await expect(mutation).resolves.toEqual(answer)
      else await expect(mutation).rejects.toBe(error)
    })

    expect(askQuestion).toHaveBeenCalledExactlyOnceWith("pooling?", 5)
  })
})
