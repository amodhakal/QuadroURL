import { test, expect } from "@playwright/test"

const hit = {
  id: 1,
  user_id: 1,
  short_code: "pool01",
  original_url: "https://example.com/pooling",
  title: "Pooling guide",
  score: 0.8731,
}

test.beforeEach(async ({ page }) => {
  await page.route(
    url => url.pathname === "/api/search",
    route => route.fulfill({ json: { kind: "search", query: "pooling", results: [hit] } }),
  )
  await page.route(
    url => url.pathname === "/api/ask",
    route => route.fulfill({
      json: { answer: "Pooling is covered by [1].", model: "test-model", sources: [hit] },
    }),
  )
})

test("searches links and shows ranked results", async ({ page }) => {
  await page.goto("/search")
  await page.getByRole("textbox", { name: "Search query" }).fill("pooling")
  await page.getByRole("button", { name: "Search" }).click()
  await expect(page.getByText("/pool01")).toBeVisible()
  await expect(page.getByLabel("Relevance 87 percent")).toBeVisible()
})

test("asks a question and shows the answer with sources", async ({ page }) => {
  await page.goto("/search")
  await page.getByRole("textbox", { name: "Question" }).fill("pooling?")
  await page.getByRole("button", { name: "Ask" }).click()
  await expect(page.getByText("Pooling is covered by [1].")).toBeVisible()
  await expect(page.getByText("test-model")).toBeVisible()
})
