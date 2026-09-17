import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ThemeToggle } from "./app/ThemeToggle"
import { ThemeProvider } from "./theme-provider"

describe("theme preferences", () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.className = "app-shell"
  })

  afterEach(() => {
    localStorage.clear()
    document.documentElement.className = ""
  })

  it.each([true, false])("uses the system color scheme (dark: %s)", (dark) => {
    const matchMedia = vi.fn().mockReturnValue({ matches: dark })
    vi.stubGlobal("matchMedia", matchMedia)
    render(<ThemeProvider><ThemeToggle /></ThemeProvider>)
    expect(matchMedia).toHaveBeenCalledWith("(prefers-color-scheme: dark)")
    expect(document.documentElement).toHaveClass(dark ? "dark" : "light", "app-shell")
  })

  it("uses a saved preference ahead of the default", () => {
    localStorage.setItem("custom-theme", "light")
    render(<ThemeProvider defaultTheme="dark" storageKey="custom-theme"><ThemeToggle /></ThemeProvider>)
    expect(document.documentElement).toHaveClass("light")
    expect(document.documentElement).not.toHaveClass("dark")
  })

  it("toggles the theme and persists it under the configured key", () => {
    render(<ThemeProvider defaultTheme="light" storageKey="custom-theme"><ThemeToggle /></ThemeProvider>)
    fireEvent.click(screen.getByRole("button", { name: "Toggle theme" }))
    expect(document.documentElement).toHaveClass("dark")
    expect(document.documentElement).not.toHaveClass("light")
    expect(localStorage.getItem("custom-theme")).toBe("dark")

    fireEvent.click(screen.getByRole("button", { name: "Toggle theme" }))
    expect(document.documentElement).toHaveClass("light")
    expect(document.documentElement).not.toHaveClass("dark")
    expect(localStorage.getItem("custom-theme")).toBe("light")
  })

  it("lets a user override the system preference", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }))
    render(<ThemeProvider><ThemeToggle /></ThemeProvider>)
    fireEvent.click(screen.getByRole("button", { name: "Toggle theme" }))
    expect(document.documentElement).toHaveClass("dark")
    expect(localStorage.getItem("quadrourl-theme")).toBe("dark")
  })
})
