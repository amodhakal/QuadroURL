// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { API_BASE } from "@/lib/api"
import { askQuestion, searchLinks } from "./search"

const fetchMock = vi.fn<typeof fetch>()
const json = (body: unknown, status = 200) => Response.json(body, { status })

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("searchLinks", () => {
  it("encodes the query with a default limit", async () => {
    const result = { kind: "search", query: "pooling", results: [] }
    fetchMock.mockResolvedValueOnce(json(result))
    await expect(searchLinks("pooling")).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `${API_BASE}/search?q=pooling&k=5`,
      { headers: { "Content-Type": "application/json" } },
    )
  })

  it("encodes special characters and a custom limit", async () => {
    const result = { kind: "search", query: "a&b", results: [] }
    fetchMock.mockResolvedValueOnce(json(result))
    await expect(searchLinks("a&b", 3)).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `${API_BASE}/search?q=a%26b&k=3`,
      { headers: { "Content-Type": "application/json" } },
    )
  })
})

describe("askQuestion", () => {
  it("posts the question and returns the answer with sources", async () => {
    const result = { answer: "Pooling is covered by [1].", model: "test-model", sources: [] }
    fetchMock.mockResolvedValueOnce(json(result))
    await expect(askQuestion("How do I pool?")).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/ask`, {
      headers: { "Content-Type": "application/json" },
      method: "POST",
      body: JSON.stringify({ question: "How do I pool?", k: 5 }),
    })
  })
})
