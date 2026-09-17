import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { toast } from "sonner"

import type { Url } from "@/types/api"
import { copyText } from "@/lib/api"
import { ShortUrlCard } from "./ShortUrlCard"

vi.mock("@/lib/api", () => ({ copyText: vi.fn() }))
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }))

const url: Url = {
  id: 1, user_id: 1, short_code: "docs", title: "Documentation",
  original_url: "https://example.com/docs", is_active: true,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
}

afterEach(() => vi.useRealTimers())

describe("ShortUrlCard", () => {
  it("copies the public link and resets its confirmation after two seconds", async () => {
    vi.useFakeTimers()
    vi.stubEnv("VITE_PUBLIC_URL", "https://short.example")
    vi.mocked(copyText).mockResolvedValue(undefined)
    render(<ShortUrlCard url={url} onDismiss={vi.fn()} />)
    expect(screen.getByText("Documentation")).toBeInTheDocument()
    expect(screen.getByText("https://short.example/urls/docs/redirect")).toBeInTheDocument()

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy" })))
    expect(copyText).toHaveBeenCalledWith("https://short.example/urls/docs/redirect")
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith("Link copied to clipboard")
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument()
  })

  it("dismisses the result when requested", () => {
    const dismiss = vi.fn()
    render(<ShortUrlCard url={url} onDismiss={dismiss} />)
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }))
    expect(dismiss).toHaveBeenCalledOnce()
  })
})
