import "@testing-library/jest-dom/vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ListResponse, User } from "@/types/api"
import { UserTable } from "./UserTable"

const mocks = vi.hoisted(() => ({
  query: { data: undefined as ListResponse<User> | undefined, isLoading: false, isError: false },
  deletion: { mutate: vi.fn(), isPending: false },
}))

vi.mock("@/hooks/use-users", () => ({
  useUsers: () => mocks.query,
  useDeleteUser: () => mocks.deletion,
}))

const owner: User = {
  id: 17,
  username: "jane",
  email: "jane@example.com",
  created_at: "2026-06-15T12:00:00",
}

beforeEach(() => {
  mocks.query.data = { kind: "users", sample: [owner] }
  mocks.query.isLoading = false
  mocks.query.isError = false
  mocks.deletion.isPending = false
  mocks.deletion.mutate.mockReset()
})

describe("UserTable", () => {
  it("renders skeletons while loading rather than an empty table", () => {
    mocks.query.isLoading = true
    mocks.query.data = undefined
    const { container } = render(<UserTable />)

    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3)
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
    expect(screen.queryByText(/No users yet/)).not.toBeInTheDocument()
  })

  it("shows a load error", () => {
    mocks.query.isError = true
    render(<UserTable />)
    expect(screen.getByText("Failed to load users. Is the API running?")).toBeInTheDocument()
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
  })

  it("shows the empty state", () => {
    mocks.query.data = { kind: "users", sample: [] }
    render(<UserTable />)
    expect(screen.getByText("No users yet. Create your first user.")).toBeInTheDocument()
  })

  it("renders user details and a formatted creation date", () => {
    render(<UserTable />)
    const row = screen.getByRole("row", { name: /jane jane@example.com/ })
    expect(within(row).getByRole("cell", { name: "jane" })).toBeInTheDocument()
    expect(within(row).getByRole("cell", { name: "Jun 15, 2026" })).toBeInTheDocument()
    expect(within(row).getByRole("button", { name: "Delete jane" })).toBeEnabled()
  })

  it("cancels deletion without mutating and confirms deletion of the selected user", async () => {
    const user = userEvent.setup()
    render(<UserTable />)

    await user.click(screen.getByRole("button", { name: "Delete jane" }))
    const dialog = screen.getByRole("alertdialog", { name: "Delete this user?" })
    expect(dialog).toHaveTextContent("jane will be permanently removed")
    expect(dialog).toHaveTextContent("Short links owned by this user are not deleted")
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Delete jane" }))
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    expect(mocks.deletion.mutate).toHaveBeenCalledExactlyOnceWith(owner.id)
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })

  it("disables confirmation while deletion is pending", async () => {
    mocks.deletion.isPending = true
    const user = userEvent.setup()
    render(<UserTable />)
    await user.click(screen.getByRole("button", { name: "Delete jane" }))
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" })
    expect(confirm).toBeDisabled()
    await user.click(confirm)
    expect(mocks.deletion.mutate).not.toHaveBeenCalled()
  })
})
