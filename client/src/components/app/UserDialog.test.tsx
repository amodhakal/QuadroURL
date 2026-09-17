import "@testing-library/jest-dom/vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/lib/api"
import { UserDialog } from "./UserDialog"

const mocks = vi.hoisted(() => ({
  creation: { mutateAsync: vi.fn(), isPending: false },
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock("@/hooks/use-users", () => ({ useCreateUser: () => mocks.creation }))
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }))

beforeEach(() => {
  mocks.creation.mutateAsync.mockReset().mockResolvedValue({ id: 17 })
  mocks.creation.isPending = false
  mocks.success.mockReset()
  mocks.error.mockReset()
})

async function fillForm() {
  const user = userEvent.setup()
  await user.type(screen.getByRole("textbox", { name: "Username" }), "  jane  ")
  await user.type(screen.getByRole("textbox", { name: "Email" }), "jane@example.com")
  return user
}

describe("UserDialog", () => {
  it("does not render a dialog when closed", () => {
    render(<UserDialog open={false} onOpenChange={vi.fn()} />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("validates required fields without creating a user", async () => {
    const user = userEvent.setup()
    render(<UserDialog open onOpenChange={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Create user" }))
    expect(await screen.findByText("Username is required")).toBeInTheDocument()
    expect(screen.getByText("Enter a valid email")).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Username" })).toHaveAttribute("aria-invalid", "true")
    expect(mocks.creation.mutateAsync).not.toHaveBeenCalled()
  })

  it("rejects whitespace-only usernames and malformed emails in the schema", async () => {
    const user = userEvent.setup()
    render(<UserDialog open onOpenChange={vi.fn()} />)
    await user.type(screen.getByRole("textbox", { name: "Username" }), "   ")
    await user.type(screen.getByRole("textbox", { name: "Email" }), "not-an-email")
    // Submit directly to exercise the schema independently of native email validation.
    fireEvent.submit(screen.getByRole("button", { name: "Create user" }).closest("form")!)
    expect(await screen.findByText("Username is required")).toBeInTheDocument()
    expect(screen.getByText("Enter a valid email")).toBeInTheDocument()
    expect(mocks.creation.mutateAsync).not.toHaveBeenCalled()
  })

  it("submits trimmed values, announces success, resets the form, and requests closure", async () => {
    const onOpenChange = vi.fn()
    render(<UserDialog open onOpenChange={onOpenChange} />)
    const user = await fillForm()
    await user.click(screen.getByRole("button", { name: "Create user" }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledExactlyOnceWith(false))
    expect(mocks.creation.mutateAsync).toHaveBeenCalledExactlyOnceWith({ username: "jane", email: "jane@example.com" })
    expect(mocks.success).toHaveBeenCalledExactlyOnceWith("User created")
    expect(mocks.error).not.toHaveBeenCalled()
    expect(screen.getByRole("textbox", { name: "Username" })).toHaveValue("")
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue("")
  })

  it.each([
    [new ApiError(409, "Email already exists"), "Email already exists"],
    [new Error("Network failed"), "Failed to create user"],
  ])("keeps the form open and preserves values on failure: %s", async (error, message) => {
    mocks.creation.mutateAsync.mockRejectedValue(error)
    const onOpenChange = vi.fn()
    render(<UserDialog open onOpenChange={onOpenChange} />)
    const user = await fillForm()
    await user.click(screen.getByRole("button", { name: "Create user" }))
    await waitFor(() => expect(mocks.error).toHaveBeenCalledExactlyOnceWith(message))
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(mocks.success).not.toHaveBeenCalled()
    expect(screen.getByRole("textbox", { name: "Username" })).toHaveValue("  jane  ")
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue("jane@example.com")
  })

  it("disables creation while the mutation is pending", async () => {
    mocks.creation.isPending = true
    render(<UserDialog open onOpenChange={vi.fn()} />)
    const user = await fillForm()
    const submit = screen.getByRole("button", { name: "Creating…" })
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(mocks.creation.mutateAsync).not.toHaveBeenCalled()
  })

  it.each(["Cancel", "Close"])("requests closure using %s without submitting", async (name) => {
    const onOpenChange = vi.fn()
    const user = userEvent.setup()
    render(<UserDialog open onOpenChange={onOpenChange} />)
    await user.click(screen.getByRole("button", { name }))
    expect(onOpenChange).toHaveBeenCalledExactlyOnceWith(false)
    expect(mocks.creation.mutateAsync).not.toHaveBeenCalled()
  })
})
