import { test, expect } from "@playwright/test"

const owner = { id: 1, username: "Alice", email: "alice@example.com", created_at: "2026-01-01" }

test.beforeEach(async ({ page }) => {
  await page.route("**/api/users**", route => route.fulfill({ json: { kind: "list", sample: [owner] } }))
  await page.route("**/api/urls**", route => route.fulfill({ json: { kind: "list", sample: [] } }))
})

test("creates a campaign link and displays safe local preview", async ({ page }) => {
  let submitted: Record<string, unknown> | undefined
  await page.route("**/api/urls", async route => {
    if (route.request().method() !== "POST") return route.fallback()
    submitted = route.request().postDataJSON()
    await route.fulfill({ status: 201, json: {
      ...submitted, id: 9, short_code: "launch9", is_active: true,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    } })
  })
  await page.goto("/")
  await page.getByLabel("Title", { exact: true }).fill("Autumn campaign")
  await page.getByLabel("Original URL").fill("https://example.com/page?keep=1")
  await page.getByRole("combobox").click()
  await page.getByRole("option", { name: "Alice" }).click()
  await page.getByLabel("Add UTM parameters").check()
  await page.getByLabel("Source", { exact: true }).fill("email & partners")
  await page.getByRole("button", { name: /shorten/i }).click()
  await expect(page.getByText("Short link created", { exact: true }).first()).toBeVisible()
  expect(submitted?.user_id).toBe(1)
  const target = new URL(String(submitted?.original_url))
  expect(target.searchParams.get("utm_source")).toBe("email & partners")
  expect(target.searchParams.get("keep")).toBe("1")
  await expect(page.getByText(/launch9/).first()).toBeVisible()
})

test("rejects unsafe schemes before making a create request", async ({ page }) => {
  let writes = 0
  page.on("request", request => { if (request.method() === "POST") writes++ })
  await page.goto("/")
  await page.getByLabel("Title", { exact: true }).fill("Invalid")
  await page.getByLabel("Original URL").fill("javascript:alert(1)")
  await page.getByRole("button", { name: /shorten/i }).click()
  await expect(page.getByText("Enter an HTTP or HTTPS URL without embedded credentials")).toBeVisible()
  expect(writes).toBe(0)
})

test("surfaces a server error without reporting success", async ({ page }) => {
  await page.route("**/api/urls", route => route.request().method() === "POST"
    ? route.fulfill({ status: 503, json: { error: "Service temporarily unavailable" } })
    : route.fallback())
  await page.goto("/")
  await page.getByLabel("Title", { exact: true }).fill("Test")
  await page.getByLabel("Original URL").fill("https://example.com")
  await page.getByRole("combobox").click()
  await page.getByRole("option", { name: "Alice" }).click()
  await page.getByRole("button", { name: /shorten/i }).click()
  await expect(page.getByText("Service temporarily unavailable").first()).toBeVisible()
  await expect(page.getByText("Short link created", { exact: true })).toHaveCount(0)
})
