// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { API_BASE, ApiError } from "@/lib/api"
import { createUrl, deleteUrl, getUrlStatus, listUrls, resolveShortCode, shortLink, updateUrl } from "./urls"

const input = { user_id: 7, original_url: "https://example.com/article", title: "Article" }
const now = new Date("2026-01-02T03:04:05.000Z")
const url = {
  ...input,
  id: 12,
  short_code: "abc123",
  is_active: true,
  created_at: now.toISOString(),
  updated_at: now.toISOString(),
}
const ready = {
  status: "ready",
  id: url.id,
  short_code: url.short_code,
  original_url: url.original_url,
  title: url.title,
}
const fetchMock = vi.fn<typeof fetch>()
const json = (body: unknown, status = 200) => Response.json(body, { status })

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe("URL requests", () => {
  it.each([
    [undefined, "/urls"],
    [{}, "/urls"],
    [{ size: 20 }, "/urls?size=20"],
    [{ offset: 15 }, "/urls?offset=15"],
    [{ size: 0, offset: 0 }, "/urls?size=0&offset=0"],
    [{ size: 10, offset: 20 }, "/urls?size=10&offset=20"],
  ])("lists URLs with parameters %j", async (params, path) => {
    const result = { kind: "urls", sample: [url] }
    fetchMock.mockResolvedValueOnce(json(result))
    await expect(listUrls(params)).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
    })
  })

  it("posts the input and returns a synchronously created URL without polling", async () => {
    fetchMock.mockResolvedValueOnce(json(url, 201))
    await expect(createUrl(input)).resolves.toEqual(url)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/urls`, {
      headers: { "Content-Type": "application/json" },
      method: "POST",
      body: JSON.stringify(input),
    })
  })

  it.each([{ title: "New title" }, { is_active: false }, { title: "Both", is_active: true }])(
    "updates only the supplied fields %j", async (patch) => {
      const updated = { ...url, ...patch }
      fetchMock.mockResolvedValueOnce(json(updated))
      await expect(updateUrl(12, patch)).resolves.toEqual(updated)
      expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/urls/12`, {
        headers: { "Content-Type": "application/json" },
        method: "PUT",
        body: JSON.stringify(patch),
      })
    },
  )

  it("deletes a URL", async () => {
    fetchMock.mockResolvedValueOnce(json({}))
    await expect(deleteUrl(12)).resolves.toEqual({})
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/urls/12`, {
      headers: { "Content-Type": "application/json" }, method: "DELETE",
    })
  })

  it("resolves a short code", async () => {
    const result = { url: input.original_url, short_code: "abc123" }
    fetchMock.mockResolvedValueOnce(json(result))
    await expect(resolveShortCode("abc123")).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/r/abc123`, {
      headers: { "Content-Type": "application/json" },
    })
  })

  it.each([
    ["list", () => listUrls()],
    ["create", () => createUrl(input)],
    ["update", () => updateUrl(12, { title: "Changed" })],
    ["delete", () => deleteUrl(12)],
    ["resolve", () => resolveShortCode("abc123")],
  ])("propagates HTTP errors from %s", async (_name, request) => {
    fetchMock.mockResolvedValueOnce(json({ error: "Not authorized" }, 401))
    await expect(request()).rejects.toMatchObject({ name: "ApiError", status: 401, message: "Not authorized" })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe("getUrlStatus", () => {
  it.each([ready, { status: "pending" }, { status: "error", error: "Creation failed" }])(
    "returns status payload %j", async (status) => {
      fetchMock.mockResolvedValueOnce(json(status))
      await expect(getUrlStatus("request-1")).resolves.toEqual(status)
      expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`${API_BASE}/urls/request-1/status`)
    },
  )

  it.each([404, 503])("treats HTTP %i as pending even with a non-JSON body", async (status) => {
    fetchMock.mockResolvedValueOnce(new Response("Unavailable", { status }))
    await expect(getUrlStatus("request-1")).resolves.toEqual({ status: "pending" })
  })

  it("preserves a server's error message and HTTP status", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: "Access denied" }, 403))
    await expect(getUrlStatus("request-1")).rejects.toEqual(new ApiError(403, "Access denied"))
  })

  it.each([{}, { error: "" }, { error: 123 }, { error: null }])(
    "uses the default message for an invalid error payload %j", async (body) => {
      fetchMock.mockResolvedValueOnce(json(body, 502))
      await expect(getUrlStatus("request-1")).rejects.toMatchObject({
        name: "ApiError", status: 502, message: "Request failed with status 502",
      })
    },
  )

  it("uses the default message for non-JSON HTTP errors", async () => {
    fetchMock.mockResolvedValueOnce(new Response("Bad gateway", { status: 502 }))
    await expect(getUrlStatus("request-1")).rejects.toMatchObject({ status: 502, message: "Request failed with status 502" })
  })

  it("propagates network failures unchanged", async () => {
    const error = new TypeError("Network unavailable")
    fetchMock.mockRejectedValueOnce(error)
    await expect(getUrlStatus("request-1")).rejects.toBe(error)
  })
})

describe("asynchronous URL creation", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    fetchMock.mockResolvedValueOnce(json({ request_id: "request-1", status: "pending" }, 202))
  })

  it("polls immediately and constructs a URL from the ready payload", async () => {
    fetchMock.mockResolvedValueOnce(json({ ...ready, title: "Server title", original_url: "https://example.com/canonical" }))
    await expect(createUrl(input)).resolves.toEqual({ ...url, title: "Server title", original_url: "https://example.com/canonical" })
    expect(fetchMock).toHaveBeenNthCalledWith(2, `${API_BASE}/urls/request-1/status`)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("retries pending, 404, and 503 results every 250ms until ready", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ status: "pending" }))
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(json(ready))
    const creation = createUrl(input)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(249)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(250)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(250)
    await expect(creation).resolves.toEqual({
      ...url, created_at: "2026-01-02T03:04:05.750Z", updated_at: "2026-01-02T03:04:05.750Z",
    })
    expect(fetchMock.mock.calls.slice(1)).toEqual(Array.from({ length: 4 }, () => [`${API_BASE}/urls/request-1/status`]))
    expect(vi.getTimerCount()).toBe(0)
  })

  it("stops polling on an application error", async () => {
    fetchMock.mockResolvedValueOnce(json({ status: "error", error: "Could not create URL" }))
    await expect(createUrl(input)).rejects.toMatchObject({ name: "ApiError", status: 500, message: "Could not create URL" })
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("stops polling on a non-retryable HTTP error", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: "Forbidden" }, 403))
    await expect(createUrl(input)).rejects.toMatchObject({ name: "ApiError", status: 403, message: "Forbidden" })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("times out after ten seconds without polling at or beyond the deadline", async () => {
    fetchMock.mockImplementation(async () => json({ status: "pending" }))
    const creation = createUrl(input)
    const rejection = expect(creation).rejects.toMatchObject({
      name: "ApiError", status: 504, message: "Timed out waiting for short link to be created",
    })
    await vi.advanceTimersByTimeAsync(9_999)
    expect(fetchMock).toHaveBeenCalledTimes(41)
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    await rejection
    expect(fetchMock).toHaveBeenCalledTimes(41)
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe("shortLink", () => {
  it("uses VITE_PUBLIC_URL when provided without requiring window", () => {
    vi.stubEnv("VITE_PUBLIC_URL", "https://sho.rt:8443")
    expect(shortLink("abc123")).toBe("https://sho.rt:8443/urls/abc123/redirect")
  })

  it.each(["http:", "https:"])("falls back to the window protocol %s and hostname, excluding its port", (protocol) => {
    vi.stubEnv("VITE_PUBLIC_URL", undefined)
    vi.stubGlobal("window", { location: { protocol, hostname: "localhost", host: "localhost:5173", port: "5173" } })
    expect(shortLink("abc123")).toBe(`${protocol}//localhost/urls/abc123/redirect`)
  })

  it("respects an explicitly empty public URL for relative links", () => {
    vi.stubEnv("VITE_PUBLIC_URL", "")
    expect(shortLink("abc123")).toBe("/urls/abc123/redirect")
  })
})
