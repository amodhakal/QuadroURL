import { api } from "@/lib/api"
import type { AskResponse, SearchResponse } from "@/types/api"

export async function searchLinks(query: string, k = 5): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, k: String(k) })
  return api<SearchResponse>(`/search?${params.toString()}`)
}

export async function askQuestion(question: string, k = 5): Promise<AskResponse> {
  return api<AskResponse>("/ask", {
    method: "POST",
    body: JSON.stringify({ question, k }),
  })
}
