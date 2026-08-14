import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createUser,
  deleteUser,
  listUsers,
  type CreateUserInput,
} from "@/api/users"

export function useUsers() {
  return useQuery({
    queryKey: ["users"],
    queryFn: () => listUsers(1, 100),
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })
}

export function useDeleteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })
}
