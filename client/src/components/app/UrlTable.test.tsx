import "@testing-library/jest-dom/vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { shortLink } from "@/api/urls"
import type { ListResponse, Url, User } from "@/types/api"
import { UrlTable } from "./UrlTable"

const mocks = vi.hoisted(() => ({
  query: { data: undefined as ListResponse<Url> | undefined, isLoading: false, isError: false },
  owners: { data: undefined as ListResponse<User> | undefined },
  toggle: { mutate: vi.fn(), isPending: false },
  deletion: { mutate: vi.fn(), isPending: false },
  copyText: vi.fn(),
  success: vi.fn(),
}))

vi.mock("@/hooks/use-urls", () => ({
  useUrls: () => mocks.query,
  useToggleUrlActive: () => mocks.toggle,
  useDeleteUrl: () => mocks.deletion,
}))
vi.mock("@/hooks/use-users", () => ({ useUsers: () => mocks.owners }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  copyText: mocks.copyText,
}))
vi.mock("sonner", () => ({ toast: { success: mocks.success } }))

const link: Url = {
  id: 31,
  user_id: 17,
  short_code: "abc123",
  original_url: "https://example.com/guide",
  title: "Example guide",
  is_active: true,
  created_at: "2026-06-15T12:00:00",
  updated_at: "2026-06-15T12:00:00",
}

beforeEach(() => {
  mocks.query.data = { kind: "urls", sample: [{ ...link }] }
  mocks.query.isLoading = false
  mocks.query.isError = false
  mocks.owners.data = { kind: "users", sample: [{ id: 17, username: "jane", email: "jane@example.com", created_at: link.created_at }] }
  mocks.toggle.isPending = false
  mocks.deletion.isPending = false
  mocks.toggle.mutate.mockReset()
  mocks.deletion.mutate.mockReset()
  mocks.copyText.mockReset().mockResolvedValue(undefined)
  mocks.success.mockReset()
})

describe("UrlTable", () => {
  it("shows loading skeletons without an empty message", () => {
    mocks.query.isLoading = true
    mocks.query.data = undefined
    const { container } = render(<UrlTable />)
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3)
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
    expect(screen.queryByText(/No links yet/)).not.toBeInTheDocument()
  })

  it("shows an API error instead of links", () => {
    mocks.query.isError = true
    render(<UrlTable />)
    expect(screen.getByText("Failed to load links. Is the API running?")).toBeInTheDocument()
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
  })

  it("invites the user to shorten a URL when empty", () => {
    mocks.query.data = { kind: "urls", sample: [] }
    render(<UrlTable />)
    expect(screen.getByText("No links yet. Shorten your first URL above.")).toBeInTheDocument()
  })

  it("renders link details, the owner, date, and a safe destination", () => {
    render(<UrlTable />)
    expect(screen.getByText("/abc123")).toBeInTheDocument()
    expect(screen.getByRole("cell", { name: "jane" })).toBeInTheDocument()
    expect(screen.getByRole("cell", { name: "Jun 15, 2026" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: link.original_url })).toHaveAttribute("href", link.original_url)
    expect(screen.getByRole("switch", { name: "Toggle abc123" })).toBeChecked()
    expect(screen.getByText("Example guide", { selector: "span" })).toBeInTheDocument()
  })

  it("falls back to the owner ID when users have not loaded", () => {
    mocks.owners.data = undefined
    render(<UrlTable />)
    expect(screen.getByRole("cell", { name: "#17" })).toBeInTheDocument()
  })

  it.each([true, false])("toggles a link whose active state is %s", async (active) => {
    mocks.query.data = { kind: "urls", sample: [{ ...link, is_active: active }] }
    const user = userEvent.setup()
    render(<UrlTable />)
    await user.click(screen.getByRole("switch", { name: "Toggle abc123" }))
    expect(mocks.toggle.mutate).toHaveBeenCalledExactlyOnceWith({ id: link.id, is_active: !active })
  })

  it("prevents toggling while a change is pending", async () => {
    mocks.toggle.isPending = true
    const user = userEvent.setup()
    render(<UrlTable />)
    const toggle = screen.getByRole("switch", { name: "Toggle abc123" })
    expect(toggle).toBeDisabled()
    await user.click(toggle)
    expect(mocks.toggle.mutate).not.toHaveBeenCalled()
  })

  it("offers the public short link and copies it from the real actions menu", async () => {
    const user = userEvent.setup()
    render(<UrlTable />)
    await user.click(screen.getByRole("button", { name: "Actions" }))
    expect(screen.getByRole("menuitem", { name: "Open" })).toHaveAttribute("href", shortLink(link.short_code))
    await user.click(screen.getByRole("menuitem", { name: "Copy link" }))
    expect(mocks.copyText).toHaveBeenCalledExactlyOnceWith(shortLink(link.short_code))
    expect(mocks.success).toHaveBeenCalledExactlyOnceWith("Link copied")
  })

  it("cancels deletion without mutating, then confirms the selected link", async () => {
    const user = userEvent.setup()
    render(<UrlTable />)
    await user.click(screen.getByRole("button", { name: "Actions" }))
    await user.click(screen.getByRole("menuitem", { name: "Delete" }))
    const dialog = screen.getByRole("alertdialog", { name: "Delete this short link?" })
    expect(dialog).toHaveTextContent("/abc123 will be permanently removed")
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Actions" }))
    await user.click(screen.getByRole("menuitem", { name: "Delete" }))
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    expect(mocks.deletion.mutate).toHaveBeenCalledExactlyOnceWith(link.id)
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })

  it("disables delete confirmation while pending", async () => {
    mocks.deletion.isPending = true
    const user = userEvent.setup()
    render(<UrlTable />)
    await user.click(screen.getByRole("button", { name: "Actions" }))
    await user.click(screen.getByRole("menuitem", { name: "Delete" }))
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" })
    expect(confirm).toBeDisabled()
    await user.click(confirm)
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()
  })
})
