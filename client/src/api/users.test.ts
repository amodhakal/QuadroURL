// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { API_BASE } from "@/lib/api"
import { createUser, deleteUser, listUsers } from "./users"

const input = { username: "Ada", email: "ada@example.com" }
const user = { ...input, id: 7, created_at: "2026-01-02T03:04:05.000Z" }
const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("listUsers", () => {
  it("requests the default first page and page size", async () => {
    const result = { kind: "users", sample: [user] }
    fetchMock.mockResolvedValueOnce(Response.json(result))
    await expect(listUsers()).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/users?page=1&per_page=100`, {
      headers: { "Content-Type": "application/json" },
    })
  })

  it.each([
    [3, 25, "page=3&per_page=25"],
    [2, undefined, "page=2&per_page=100"],
    [undefined, 15, "page=1&per_page=15"],
    [0, 0, "page=0&per_page=0"],
  ])("passes page %s and page size %s", async (page, perPage, query) => {
    const result = { kind: "users", sample: [] }
    fetchMock.mockResolvedValueOnce(Response.json(result))
    await expect(listUsers(page, perPage)).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/users?${query}`, {
      headers: { "Content-Type": "application/json" },
    })
  })
})

describe("createUser", () => {
  it("posts JSON and returns the server-created user", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(user, { status: 201 }))
    await expect(createUser(input)).resolves.toEqual(user)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/users`, {
      headers: { "Content-Type": "application/json" },
      method: "POST",
      body: JSON.stringify(input),
    })
  })
})

describe("deleteUser", () => {
  it("deletes the requested user without a body", async () => {
    fetchMock.mockResolvedValueOnce(Response.json({}))
    await expect(deleteUser(7)).resolves.toEqual({})
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/users/7`, {
      headers: { "Content-Type": "application/json" },
      method: "DELETE",
    })
  })
})

describe.each([
  ["listUsers", () => listUsers()],
  ["createUser", () => createUser(input)],
  ["deleteUser", () => deleteUser(7)],
])("%s failures", (_name, request) => {
  it("propagates the HTTP status and server error", async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ error: "Not authorized" }, { status: 401 }))
    await expect(request()).rejects.toMatchObject({ name: "ApiError", status: 401, message: "Not authorized" })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("falls back to the status message for a non-JSON error", async () => {
    fetchMock.mockResolvedValueOnce(new Response("Service failure", { status: 500 }))
    await expect(request()).rejects.toMatchObject({ name: "ApiError", status: 500, message: "Request failed with status 500" })
  })

  it("propagates network failures unchanged", async () => {
    const error = new TypeError("Failed to fetch")
    fetchMock.mockRejectedValueOnce(error)
    await expect(request()).rejects.toBe(error)
  })
})
