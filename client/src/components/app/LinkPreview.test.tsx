import { render, screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { LinkPreview } from "./LinkPreview"

describe("LinkPreview", () => {
  it("shows a local preview with a safe external destination link without fetching metadata", () => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    render(<LinkPreview url="https://example.com:8080/docs?source=email#intro" title="  Documentation  " />)
    const preview = within(screen.getByRole("region", { name: "Destination preview" }))
    expect(preview.getByText("Documentation")).toBeInTheDocument()
    expect(preview.getByText("example.com:8080")).toBeInTheDocument()
    const link = preview.getByRole("link", { name: "Open destination" })
    expect(link).toHaveAttribute("href", "https://example.com:8080/docs?source=email#intro")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([undefined, "   "])("uses the hostname when title is %j", (title) => {
    render(<LinkPreview url="https://example.com:8080/docs" title={title} />)
    expect(screen.getByText("example.com")).toBeInTheDocument()
  })

  it.each(["", "/relative", "javascript:alert(1)", "https://user:pass@example.com"])(
    "does not expose an invalid destination as a link: %s",
    (url) => {
      render(<LinkPreview url={url} />)
      expect(screen.queryByRole("link")).not.toBeInTheDocument()
      expect(screen.getByText(/Enter an HTTP or HTTPS URL/)).toBeInTheDocument()
    },
  )
})
