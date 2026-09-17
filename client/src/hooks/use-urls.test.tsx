import type { ReactNode } from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createUrl, deleteUrl, listUrls, updateUrl } from "@/api/urls"
import type { Url } from "@/types/api"
import {
  useCreateUrl,
  useDeleteUrl,
  useToggleUrlActive,
  useUrls,
} from "./use-urls"

vi.mock("@/api/urls", () => ({
  createUrl: vi.fn(),
  deleteUrl: vi.fn(),
  listUrls: vi.fn(),
  updateUrl: vi.fn(),
}))

const url: Url = {
  id: 42,
  user_id: 7,
  short_code: "abc123",
  original_url: "https://example.com/article",
  title: "An article",
  is_active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
}
const response = { kind: "urls", sample: [url] }
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
  queryClient.setQueryData(["urls"], response)
  queryClient.setQueryData(["users"], { kind: "users", sample: [] })
  return vi.spyOn(queryClient, "invalidateQueries")
}

function expectCacheInvalidation(succeeded: boolean) {
  expect(queryClient.getQueryState(["urls"])?.isInvalidated).toBe(succeeded)
  expect(queryClient.getQueryState(["users"])?.isInvalidated).toBe(false)
}

describe("useUrls", () => {
  it("loads the first 100 URLs and stores the response under the URLs key", async () => {
    vi.mocked(listUrls).mockResolvedValue(response)
    const { result } = renderHook(() => useUrls(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(listUrls).toHaveBeenCalledExactlyOnceWith({ size: 100 })
    expect(result.current.data).toEqual(response)
    expect(queryClient.getQueryData(["urls"])).toEqual(response)
  })

  it("exposes a query failure without retrying", async () => {
    const error = new Error("Cannot load URLs")
    vi.mocked(listUrls).mockRejectedValue(error)
    const { result } = renderHook(() => useUrls(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error).toBe(error)
    expect(result.current.data).toBeUndefined()
    expect(listUrls).toHaveBeenCalledExactlyOnceWith({ size: 100 })
  })
})

describe("useCreateUrl", () => {
  it.each([true, false])("forwards input and invalidates only on success (success=%s)", async (succeeded) => {
    const input = { user_id: 7, original_url: url.original_url, title: url.title }
    const error = new Error("Cannot create URL")
    if (succeeded) vi.mocked(createUrl).mockResolvedValue(url)
    else vi.mocked(createUrl).mockRejectedValue(error)
    const invalidate = seedCache()
    const { result } = renderHook(() => useCreateUrl(), { wrapper })

    await act(async () => {
      if (succeeded) await expect(result.current.mutateAsync(input)).resolves.toEqual(url)
      else await expect(result.current.mutateAsync(input)).rejects.toBe(error)
    })
    await waitFor(() => expect(result.current.status).toBe(succeeded ? "success" : "error"))

    expect(createUrl).toHaveBeenCalledExactlyOnceWith(input)
    if (succeeded) expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ["urls"] })
    else expect(invalidate).not.toHaveBeenCalled()
    expectCacheInvalidation(succeeded)
  })
})

describe("useToggleUrlActive", () => {
  it.each([
    { succeeded: true, is_active: false },
    { succeeded: true, is_active: true },
    { succeeded: false, is_active: false },
    { succeeded: false, is_active: true },
  ])("updates is_active=$is_active and invalidates only on success (success=$succeeded)", async ({ succeeded, is_active }) => {
    const updated = { ...url, is_active }
    const error = new Error("Cannot update URL")
    if (succeeded) vi.mocked(updateUrl).mockResolvedValue(updated)
    else vi.mocked(updateUrl).mockRejectedValue(error)
    const invalidate = seedCache()
    const { result } = renderHook(() => useToggleUrlActive(), { wrapper })

    await act(async () => {
      const mutation = result.current.mutateAsync({ id: url.id, is_active })
      if (succeeded) await expect(mutation).resolves.toEqual(updated)
      else await expect(mutation).rejects.toBe(error)
    })
    await waitFor(() => expect(result.current.status).toBe(succeeded ? "success" : "error"))

    expect(updateUrl).toHaveBeenCalledExactlyOnceWith(url.id, { is_active })
    if (succeeded) expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ["urls"] })
    else expect(invalidate).not.toHaveBeenCalled()
    expectCacheInvalidation(succeeded)
  })
})

describe("useDeleteUrl", () => {
  it.each([true, false])("forwards the ID and invalidates only on success (success=%s)", async (succeeded) => {
    const error = new Error("Cannot delete URL")
    if (succeeded) vi.mocked(deleteUrl).mockResolvedValue({})
    else vi.mocked(deleteUrl).mockRejectedValue(error)
    const invalidate = seedCache()
    const { result } = renderHook(() => useDeleteUrl(), { wrapper })

    await act(async () => {
      if (succeeded) await expect(result.current.mutateAsync(url.id)).resolves.toEqual({})
      else await expect(result.current.mutateAsync(url.id)).rejects.toBe(error)
    })
    await waitFor(() => expect(result.current.status).toBe(succeeded ? "success" : "error"))

    expect(deleteUrl).toHaveBeenCalledExactlyOnceWith(url.id)
    if (succeeded) expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ["urls"] })
    else expect(invalidate).not.toHaveBeenCalled()
    expectCacheInvalidation(succeeded)
  })
})
