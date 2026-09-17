import { describe, expect, it } from "vitest"

import { buildCampaignUrl, parseDestination } from "./link-tools"

describe("parseDestination", () => {
  it("accepts absolute HTTP(S) URLs without credentials", () => {
    expect(parseDestination("https://example.com/page")?.href).toBe("https://example.com/page")
    expect(parseDestination("http://example.com")?.protocol).toBe("http:")
  })

  it("rejects unsafe or non-absolute shapes", () => {
    for (const value of [
      "javascript:alert(1)",
      "data:text/html,hi",
      "ftp://example.com/file",
      "example.com/page",
      "/relative/path",
      "https://user:pass@example.com",
      "https://example.com\\@evil.com",
      "  https://example.com/with space",
      "",
      "https://",
      "https://[invalid]/",
      "https://example.com/\u007fpath",
      "https://:pass@example.com/",
    ]) {
      expect(parseDestination(value), value).toBeNull()
    }
  })
})

describe("buildCampaignUrl", () => {
  it("leaves the destination untouched when tracking is disabled", () => {
    const url = "https://example.com/page?keep=1"
    expect(buildCampaignUrl(url)).toBe(url)
  })

  it("encodes UTM values while preserving existing parameters", () => {
    const result = new URL(
      buildCampaignUrl("https://example.com/page?keep=1", {
        utm_source: "email & partners",
      })!,
    )
    expect(result.searchParams.get("utm_source")).toBe("email & partners")
    expect(result.searchParams.get("keep")).toBe("1")
  })

  it("deduplicates repeated UTM keys and keeps blank fields intact", () => {
    const result = new URL(
      buildCampaignUrl("https://example.com/?utm_source=a&utm_source=b", {
        utm_source: "",
      })!,
    )
    expect(result.searchParams.getAll("utm_source")).toEqual(["a"])
  })

  it("rejects unsafe destinations before any rewriting", () => {
    expect(buildCampaignUrl("javascript:alert(1)", { utm_source: "x" })).toBeNull()
  })
})
