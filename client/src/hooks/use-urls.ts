import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createUrl,
  deleteUrl,
  listUrls,
  updateUrl,
  type CreateUrlInput,
} from "@/api/urls"

export function useUrls() {
  return useQuery({
    queryKey: ["urls"],
    queryFn: () => listUrls({ size: 100 }),
  })
}

export function useCreateUrl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateUrlInput) => createUrl(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["urls"] })
    },
  })
}

export function useToggleUrlActive() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      updateUrl(id, { is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["urls"] })
    },
  })
}

export function useDeleteUrl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteUrl(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["urls"] })
    },
  })
}
