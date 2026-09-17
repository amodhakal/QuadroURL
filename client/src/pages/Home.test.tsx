import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

import { API_BASE } from "@/lib/api"
import type { Url, User } from "@/types/api"
import { Home } from "./Home"

const owner: User = {
  id: 17,
  username: "jane",
  email: "jane@example.com",
  created_at: "2026-06-15T12:00:00Z",
}
const created: Url = {
  id: 31,
  user_id: owner.id,
  short_code: "abc123",
  original_url: "https://example.com/guide",
  title: "Example guide",
  is_active: true,
  created_at: "2026-06-15T12:00:00Z",
  updated_at: "2026-06-15T12:00:00Z",
}
const emptyMessage = "No links yet. Shorten your first URL above."
const fetchMock = vi.fn<typeof fetch>()
let queryClient: QueryClient

// Radix Select uses browser APIs that jsdom does not implement.
const selectPolyfills = {
  scrollIntoView: () => {},
  hasPointerCapture: () => false,
  setPointerCapture: () => {},
  releasePointerCapture: () => {},
}
const originalDescriptors = new Map<string, PropertyDescriptor | undefined>()
beforeAll(() => {
  for (const [key, value] of Object.entries(selectPolyfills)) {
    originalDescriptors.set(key, Object.getOwnPropertyDescriptor(HTMLElement.prototype, key))
    if (!(key in HTMLElement.prototype)) {
      Object.defineProperty(HTMLElement.prototype, key, { configurable: true, value })
    }
  }
})
afterAll(() => {
  for (const [key, descriptor] of originalDescriptors) {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor)
    else Reflect.deleteProperty(HTMLElement.prototype, key)
  }
})

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function deferredResponse() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => { resolve = done })
  return { promise, resolve }
}

function serve({
  list = () => json({ kind: "urls", sample: [] }),
  create,
}: {
  list?: () => Response | Promise<Response>
  create?: (options: RequestInit) => Response | Promise<Response>
} = {}) {
  fetchMock.mockImplementation(async (input, options) => {
    if (input === `${API_BASE}/users?page=1&per_page=100`) {
      return json({ kind: "users", sample: [owner] })
    }
    if (input === `${API_BASE}/urls?size=100`) return list()
    if (input === `${API_BASE}/urls` && options?.method === "POST" && create) {
      return create(options)
    }
    throw new Error(`Unexpected request: ${options?.method ?? "GET"} ${input}`)
  })
}

function renderHome() {
  return render(
    <QueryClientProvider client={queryClient}>
      <Home />
      <Toaster theme="light" duration={Infinity} />
    </QueryClientProvider>,
  )
}

async function fillForm(user: ReturnType<typeof userEvent.setup>, destination = created.original_url) {
  await user.type(screen.getByRole("textbox", { name: "Title" }), `  ${created.title}  `)
  await user.type(screen.getByRole("textbox", { name: "Original URL" }), destination)
  await user.click(screen.getByRole("combobox", { name: "Owner" }))
  await user.click(await screen.findByRole("option", { name: owner.username }))
}

function creationRequests() {
  return fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  serve()
})
afterEach(() => {
  queryClient.clear()
})

describe("Home integration", () => {
  it("shows loading before inviting the user to create their first link", async () => {
    const response = deferredResponse()
    serve({ list: () => response.promise })
    const { container } = renderHome()

    expect(screen.getByRole("heading", { name: "Shorten a link" })).toBeInTheDocument()
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3)
    expect(screen.queryByText(emptyMessage)).not.toBeInTheDocument()
    expect(screen.queryByRole("table")).not.toBeInTheDocument()

    await act(async () => { response.resolve(json({ kind: "urls", sample: [] })) })
    expect(await screen.findByText(emptyMessage)).toBeInTheDocument()
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeInTheDocument()
    expect(screen.queryByText("Your short link is ready")).not.toBeInTheDocument()
  })

  it("shows a links API failure while leaving the creation form available", async () => {
    serve({ list: () => json({ error: "Unavailable" }, 503) })
    renderHome()

    expect(await screen.findByText("Failed to load links. Is the API running?")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Shorten link" })).toBeEnabled()
    expect(screen.queryByText(emptyMessage)).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([input]) => input === `${API_BASE}/urls?size=100`)).toHaveLength(1)
  })

  it("validates required fields and rejects unsafe destinations without sending a creation request", async () => {
    const user = userEvent.setup()
    renderHome()
    await screen.findByText(emptyMessage)
    await user.click(screen.getByRole("button", { name: "Shorten link" }))

    expect(await screen.findByText("Title is required")).toBeInTheDocument()
    expect(screen.getByText("URL is required")).toBeInTheDocument()
    expect(screen.getByText("Select an owner")).toBeInTheDocument()
    expect(creationRequests()).toHaveLength(0)

    await fillForm(user, "https://user:secret@example.com/private")
    await user.click(screen.getByRole("button", { name: "Shorten link" }))
    expect(await screen.findByText("Enter an HTTP or HTTPS URL without embedded credentials")).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Original URL" })).toHaveAttribute("aria-invalid", "true")
    expect(screen.queryByRole("link", { name: "Open destination" })).not.toBeInTheDocument()
    expect(creationRequests()).toHaveLength(0)
  })

  it("creates a campaign link, refreshes the table, resets all fields, and dismisses only the result card", async () => {
    const user = userEvent.setup()
    const response = deferredResponse()
    const destination = "https://example.com/guide?ref=sidebar&ref=footer&utm_source=old&utm_source=duplicate&utm_medium=email#details"
    const expectedDestination = "https://example.com/guide?ref=sidebar&ref=footer&utm_source=newsletter&utm_medium=email&utm_campaign=autumn+launch#details"
    const result = { ...created, original_url: expectedDestination }
    let links: Url[] = []
    serve({
      list: () => json({ kind: "urls", sample: links }),
      create: () => response.promise,
    })
    renderHome()
    await screen.findByText(emptyMessage)
    await fillForm(user, destination)
    await user.click(screen.getByRole("checkbox", { name: "Add UTM parameters" }))
    await user.type(screen.getByRole("textbox", { name: "Source" }), " newsletter ")
    await user.type(screen.getByRole("textbox", { name: "Campaign" }), "autumn launch")
    expect(screen.getByRole("link", { name: "Open destination" })).toHaveAttribute("href", expectedDestination)
    await user.click(screen.getByRole("button", { name: "Shorten link" }))

    expect(await screen.findByRole("button", { name: "Shortening…" })).toBeDisabled()
    expect(screen.getByRole("checkbox", { name: "Add UTM parameters" })).toBeDisabled()
    expect(creationRequests()).toHaveLength(1)
    expect(JSON.parse(String(creationRequests()[0][1]?.body))).toEqual({
      user_id: owner.id,
      title: created.title,
      original_url: expectedDestination,
    })
    links = [result]
    await act(async () => { response.resolve(json(result, 201)) })

    expect(await screen.findByText("Your short link is ready")).toBeInTheDocument()
    expect(await screen.findByText("Short link created")).toBeInTheDocument()
    expect(screen.getByText(/\/urls\/abc123\/redirect$/, { selector: "code" })).toBeInTheDocument()
    const table = await screen.findByRole("table")
    expect(within(table).getByRole("cell", { name: owner.username })).toBeInTheDocument()
    expect(within(table).getByRole("link", { name: expectedDestination })).toHaveAttribute("href", expectedDestination)
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("")
    expect(screen.getByRole("textbox", { name: "Original URL" })).toHaveValue("")
    expect(screen.getByRole("combobox", { name: "Owner" })).toHaveTextContent("Select a user")
    expect(screen.getByRole("checkbox", { name: "Add UTM parameters" })).not.toBeChecked()
    expect(screen.queryByRole("textbox", { name: "Source" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("checkbox", { name: "Add UTM parameters" }))
    expect(screen.getByRole("textbox", { name: "Source" })).toHaveValue("")
    expect(screen.getByRole("textbox", { name: "Campaign" })).toHaveValue("")

    await user.click(screen.getByRole("button", { name: "Dismiss" }))
    expect(screen.queryByText("Your short link is ready")).not.toBeInTheDocument()
    expect(within(table).getByText("/abc123")).toBeInTheDocument()
  })

  it.each([
    { name: "API", create: () => json({ error: "Owner is no longer available" }, 422), message: "Owner is no longer available" },
    { name: "network", create: () => Promise.reject(new TypeError("Failed to fetch")), message: "Failed to create short link" },
  ])("preserves input and permits retry after a failed $name request", async ({ create, message }) => {
    const user = userEvent.setup()
    serve({ create })
    renderHome()
    await screen.findByText(emptyMessage)
    await fillForm(user)
    await user.click(screen.getByRole("checkbox", { name: "Add UTM parameters" }))
    await user.type(screen.getByRole("textbox", { name: "Source" }), "newsletter")
    await user.click(screen.getByRole("button", { name: "Shorten link" }))

    expect(await screen.findByText(message)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole("button", { name: "Shorten link" })).toBeEnabled())
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(`  ${created.title}  `)
    expect(screen.getByRole("textbox", { name: "Original URL" })).toHaveValue(created.original_url)
    expect(screen.getByRole("combobox", { name: "Owner" })).toHaveTextContent(owner.username)
    expect(screen.getByRole("checkbox", { name: "Add UTM parameters" })).toBeChecked()
    expect(screen.getByRole("textbox", { name: "Source" })).toHaveValue("newsletter")
    expect(screen.queryByText("Your short link is ready")).not.toBeInTheDocument()
    expect(screen.getByText(emptyMessage)).toBeInTheDocument()
    expect(creationRequests()).toHaveLength(1)

    serve({ create: () => json(created, 201) })
    await user.click(screen.getByRole("button", { name: "Shorten link" }))
    expect(await screen.findByText("Your short link is ready")).toBeInTheDocument()
    expect(creationRequests()).toHaveLength(2)
  })
})
