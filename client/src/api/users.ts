import { api } from "@/lib/api"
import type { ListResponse, User } from "@/types/api"

export interface CreateUserInput {
  username: string
  email: string
}

export async function listUsers(page = 1, perPage = 100): Promise<ListResponse<User>> {
  return api<ListResponse<User>>(`/users?page=${page}&per_page=${perPage}`)
}

export async function createUser(input: CreateUserInput): Promise<User> {
  return api<User>("/users", {
    method: "POST",
    body: JSON.stringify(input),
  })
}

export async function deleteUser(id: number): Promise<Record<string, never>> {
  return api<Record<string, never>>(`/users/${id}`, {
    method: "DELETE",
  })
}
