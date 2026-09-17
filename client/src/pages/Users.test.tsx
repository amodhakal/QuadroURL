import "@testing-library/jest-dom/vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { createUser, deleteUser, listUsers } from "@/api/users"
import type { User } from "@/types/api"
import { Users } from "./Users"

vi.mock("@/api/users", () => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  deleteUser: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const owner: User = {
  id: 17,
  username: "jane",
  email: "jane@example.com",
  created_at: "2026-06-15T12:00:00",
}

function renderUsers() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}><Users /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(listUsers).mockReset().mockResolvedValue({ kind: "users", sample: [] })
  vi.mocked(createUser).mockReset().mockResolvedValue(owner)
  vi.mocked(deleteUser).mockReset().mockResolvedValue({})
})

describe("Users", () => {
  it("renders the page and opens and cancels the real user dialog", async () => {
    const user = userEvent.setup()
    renderUsers()
    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument()
    expect(screen.getByText("Manage the owners of short links.")).toBeInTheDocument()
    expect(await screen.findByText("No users yet. Create your first user.")).toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "New user" }))
    const dialog = screen.getByRole("dialog", { name: "New user" })
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(createUser).not.toHaveBeenCalled()
  })

  it("creates a user, closes the dialog, and refreshes the table through the real hooks", async () => {
    const user = userEvent.setup()
    renderUsers()
    await screen.findByText("No users yet. Create your first user.")
    await user.click(screen.getByRole("button", { name: "New user" }))
    await user.type(screen.getByRole("textbox", { name: "Username" }), owner.username)
    await user.type(screen.getByRole("textbox", { name: "Email" }), owner.email)
    vi.mocked(listUsers).mockResolvedValue({ kind: "users", sample: [owner] })
    await user.click(screen.getByRole("button", { name: "Create user" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(createUser).toHaveBeenCalledExactlyOnceWith({ username: owner.username, email: owner.email })
    expect(await screen.findByRole("cell", { name: owner.email })).toBeInTheDocument()
    expect(listUsers).toHaveBeenCalledTimes(2)

    await user.click(screen.getByRole("button", { name: "New user" }))
    expect(screen.getByRole("textbox", { name: "Username" })).toHaveValue("")
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue("")
  })

  it("deletes a user and refreshes the table after confirmation", async () => {
    vi.mocked(listUsers).mockResolvedValueOnce({ kind: "users", sample: [owner] })
    const user = userEvent.setup()
    renderUsers()
    await user.click(await screen.findByRole("button", { name: "Delete jane" }))
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    expect(await screen.findByText("No users yet. Create your first user.")).toBeInTheDocument()
    expect(deleteUser).toHaveBeenCalledExactlyOnceWith(owner.id)
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })
})
