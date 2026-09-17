import { describe, expect, it, vi } from "vitest"

import { api, ApiError, API_BASE, copyText } from "./api"

describe("api", () => {
  it("requests JSON and returns the parsed response", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ value: 42 }))
    vi.stubGlobal("fetch", fetch)

    await expect(api("/example")).resolves.toEqual({ value: 42 })
    expect(fetch).toHaveBeenCalledWith(`${API_BASE}/example`, {
      headers: { "Content-Type": "application/json" },
    })
  })

  it("forwards request options", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({}))
    vi.stubGlobal("fetch", fetch)
    const options = {
      method: "POST",
      body: JSON.stringify({ title: "Docs" }),
      signal: new AbortController().signal,
      headers: { Authorization: "Bearer test-token" },
    }
    await api("/example", options)
    expect(fetch).toHaveBeenCalledWith(`${API_BASE}/example`, options)
  })

  it("preserves the server's error and HTTP status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      Response.json({ error: "Title is required" }, { status: 422 }),
    ))
    await expect(api("/example")).rejects.toMatchObject({
      name: "ApiError", status: 422, message: "Title is required",
    })
  })

  it.each([{}, { error: "" }, { error: 123 }, null])(
    "falls back to the HTTP status for an unusable error body: %j",
    async (body) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(body, { status: 500 })))
      await expect(api("/example")).rejects.toEqual(
        new ApiError(500, "Request failed with status 500"),
      )
    },
  )

  it("handles a non-JSON error response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Unavailable", { status: 503 })))
    await expect(api("/example")).rejects.toMatchObject({
      status: 503, message: "Request failed with status 503",
    })
  })

  it("propagates network failures", async () => {
    const error = new TypeError("Failed to fetch")
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error))
    await expect(api("/example")).rejects.toBe(error)
  })

  it("does not hide malformed JSON on successful responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json")))
    await expect(api("/example")).rejects.toBeInstanceOf(SyntaxError)
  })
})

describe("copyText", () => {
  it("uses the Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    await copyText("https://example.com/short")
    expect(writeText).toHaveBeenCalledWith("https://example.com/short")
    expect(document.querySelector("textarea")).toBeNull()
  })

  it.each(["unavailable", "rejected"])("falls back when clipboard is %s and removes its textarea", async (reason) => {
    vi.stubGlobal("navigator", reason === "unavailable" ? {} : {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("Permission denied")) },
    })
    // jsdom does not implement the legacy browser copy command.
    const command = vi.fn(() => {
      const textarea = document.querySelector("textarea")
      expect(textarea?.value).toBe("Copied text")
      expect(textarea?.selectionStart).toBe(0)
      expect(textarea?.selectionEnd).toBe(11)
      return true
    })
    Object.defineProperty(document, "execCommand", { configurable: true, value: command })
    try {
      await copyText("Copied text")
      expect(command).toHaveBeenCalledWith("copy")
      expect(document.querySelector("textarea")).toBeNull()
    } finally {
      Reflect.deleteProperty(document, "execCommand")
    }
  })
})
