import type { ReactNode } from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createUser, deleteUser, listUsers } from "@/api/users"
import type { User } from "@/types/api"
import { useCreateUser, useDeleteUser, useUsers } from "./use-users"

vi.mock("@/api/users", () => ({
  createUser: vi.fn(),
  deleteUser: vi.fn(),
  listUsers: vi.fn(),
}))

const user: User = {
  id: 7,
  username: "alex",
  email: "alex@example.com",
  created_at: "2026-09-01T00:00:00Z",
}
const response = { kind: "users", sample: [user] }
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

function seedCache() {
  queryClient.setQueryData(["users"], response)
  queryClient.setQueryData(["urls"], { kind: "urls", sample: [] })
  return vi.spyOn(queryClient, "invalidateQueries")
}

function expectCacheInvalidation(succeeded: boolean) {
  expect(queryClient.getQueryState(["users"])?.isInvalidated).toBe(succeeded)
  expect(queryClient.getQueryState(["urls"])?.isInvalidated).toBe(false)
}

describe("useUsers", () => {
  it("loads page 1 with 100 users and stores the response under the users key", async () => {
    vi.mocked(listUsers).mockResolvedValue(response)
    const { result } = renderHook(() => useUsers(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(listUsers).toHaveBeenCalledExactlyOnceWith(1, 100)
    expect(result.current.data).toEqual(response)
    expect(queryClient.getQueryData(["users"])).toEqual(response)
  })

  it("exposes a query failure without retrying", async () => {
    const error = new Error("Cannot load users")
    vi.mocked(listUsers).mockRejectedValue(error)
    const { result } = renderHook(() => useUsers(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error).toBe(error)
    expect(result.current.data).toBeUndefined()
    expect(listUsers).toHaveBeenCalledExactlyOnceWith(1, 100)
  })
})

describe("useCreateUser", () => {
  it.each([true, false])("forwards input and invalidates only on success (success=%s)", async (succeeded) => {
    const input = { username: user.username, email: user.email }
    const error = new Error("Cannot create user")
    if (succeeded) vi.mocked(createUser).mockResolvedValue(user)
    else vi.mocked(createUser).mockRejectedValue(error)
    const invalidate = seedCache()
    const { result } = renderHook(() => useCreateUser(), { wrapper })

    await act(async () => {
      if (succeeded) await expect(result.current.mutateAsync(input)).resolves.toEqual(user)
      else await expect(result.current.mutateAsync(input)).rejects.toBe(error)
    })
    await waitFor(() => expect(result.current.status).toBe(succeeded ? "success" : "error"))

    expect(createUser).toHaveBeenCalledExactlyOnceWith(input)
    if (succeeded) expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ["users"] })
    else expect(invalidate).not.toHaveBeenCalled()
    expectCacheInvalidation(succeeded)
  })
})

describe("useDeleteUser", () => {
  it.each([true, false])("forwards the ID and invalidates only on success (success=%s)", async (succeeded) => {
    const error = new Error("Cannot delete user")
    if (succeeded) vi.mocked(deleteUser).mockResolvedValue({})
    else vi.mocked(deleteUser).mockRejectedValue(error)
    const invalidate = seedCache()
    const { result } = renderHook(() => useDeleteUser(), { wrapper })

    await act(async () => {
      if (succeeded) await expect(result.current.mutateAsync(user.id)).resolves.toEqual({})
      else await expect(result.current.mutateAsync(user.id)).rejects.toBe(error)
    })
    await waitFor(() => expect(result.current.status).toBe(succeeded ? "success" : "error"))

    expect(deleteUser).toHaveBeenCalledExactlyOnceWith(user.id)
    if (succeeded) expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ["users"] })
    else expect(invalidate).not.toHaveBeenCalled()
    expectCacheInvalidation(succeeded)
  })
})
