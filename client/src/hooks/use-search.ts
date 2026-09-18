import { useMutation, useQuery } from "@tanstack/react-query"

import { askQuestion, searchLinks } from "@/api/search"

export function useSemanticSearch(query: string, k = 5) {
  return useQuery({
    queryKey: ["search", query, k],
    queryFn: () => searchLinks(query, k),
    enabled: query.trim().length > 0,
    staleTime: 60_000,
  })
}

export function useAskQuestion() {
  return useMutation({
    mutationFn: ({ question, k = 5 }: { question: string; k?: number }) =>
      askQuestion(question, k),
  })
}
