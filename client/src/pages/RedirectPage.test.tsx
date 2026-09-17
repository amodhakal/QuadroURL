import { act, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { resolveShortCode } from "@/api/urls"
import type { ShortCodeResponse } from "@/types/api"
import { RedirectPage } from "./RedirectPage"

vi.mock("@/api/urls", () => ({ resolveShortCode: vi.fn() }))

function renderRedirect(path = "/r/docs") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/r/:code" element={<RedirectPage />} />
        <Route path="/" element={<RedirectPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(resolveShortCode).mockReset()
})

describe("RedirectPage", () => {
  it("shows progress while resolving the route code", () => {
    vi.mocked(resolveShortCode).mockReturnValue(new Promise(() => {}))
    renderRedirect()
    expect(screen.getByText("Redirecting you…")).toBeInTheDocument()
    expect(resolveShortCode).toHaveBeenCalledWith("docs")
  })

  it("replaces the current location with the resolved destination", async () => {
    const replace = vi.fn()
    // jsdom cannot perform navigation; keep other window APIs intact.
    vi.stubGlobal("window", new Proxy(window, {
      get(target, key) {
        return key === "location" ? { replace } : Reflect.get(target, key)
      },
    }))
    vi.mocked(resolveShortCode).mockResolvedValue({ short_code: "docs", url: "https://example.com/docs" })
    await act(async () => { renderRedirect() })
    expect(replace).toHaveBeenCalledExactlyOnceWith("https://example.com/docs")
  })

  it("offers a way home when resolving fails", async () => {
    vi.mocked(resolveShortCode).mockRejectedValue(new Error("Not found"))
    renderRedirect()
    expect(await screen.findByText("This short link doesn't exist or is no longer active.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Back home" })).toHaveAttribute("href", "/")
    expect(screen.queryByText("Redirecting you…")).not.toBeInTheDocument()
  })

  it("does not resolve a missing route code", () => {
    renderRedirect("/")
    expect(resolveShortCode).not.toHaveBeenCalled()
  })

  it.each(["success", "failure"])("ignores a late %s after unmount", async (outcome) => {
    let resolve!: (value: ShortCodeResponse) => void
    let reject!: (error: Error) => void
    vi.mocked(resolveShortCode).mockReturnValue(new Promise((res, rej) => {
      resolve = res
      reject = rej
    }))
    const replace = vi.fn()
    vi.stubGlobal("window", new Proxy(window, {
      get(target, key) {
        return key === "location" ? { replace } : Reflect.get(target, key)
      },
    }))
    const view = renderRedirect()
    view.unmount()
    await act(async () => {
      if (outcome === "success") resolve({ short_code: "docs", url: "https://example.com" })
      else reject(new Error("Not found"))
    })
    expect(replace).not.toHaveBeenCalled()
    expect(screen.queryByText(/no longer active/)).not.toBeInTheDocument()
  })
})
