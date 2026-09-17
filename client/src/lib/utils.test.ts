import { describe, expect, it } from "vitest"

import { cn } from "./utils"

describe("cn", () => {
  it("combines conditional classes and lets later Tailwind utilities win", () => {
    expect(cn("p-2 text-sm", ["rounded", false], { hidden: false, block: true }, "p-4")).toBe(
      "text-sm rounded block p-4",
    )
  })

  it("handles missing classes", () => {
    expect(cn(null, undefined, false)).toBe("")
  })
})
