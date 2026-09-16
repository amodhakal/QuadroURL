export const utmFields = [
  { key: "utm_source", label: "Source", placeholder: "newsletter" },
  { key: "utm_medium", label: "Medium", placeholder: "email" },
  { key: "utm_campaign", label: "Campaign", placeholder: "autumn launch" },
  { key: "utm_name", label: "Name", placeholder: "Optional tracking name" },
  { key: "utm_term", label: "Term", placeholder: "Optional keyword" },
  { key: "utm_content", label: "Content", placeholder: "Optional variant" },
] as const

export type UtmValues = Partial<Record<(typeof utmFields)[number]["key"], string>>

// Shared by validation and every destination link. Never fetch preview metadata.
export function parseDestination(value: string): URL | null {
  const trimmed = value.trim()
  // Require an explicit absolute URL rather than accepting URL parser repairs.
  const hasWhitespaceOrControl = Array.from(trimmed).some(
    (character) => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127,
  )
  if (!/^https?:\/\//i.test(trimmed) || trimmed.includes("\\") || hasWhitespaceOrControl) {
    return null
  }
  try {
    const url = new URL(trimmed)
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) {
      return null
    }
    return url
  } catch {
    return null
  }
}

export function buildCampaignUrl(value: string, fields?: UtmValues): string | null {
  const url = parseDestination(value)
  if (!url) return null
  // Disabled tracking must not rewrite signed or otherwise encoding-sensitive URLs.
  if (!fields) return value.trim()

  let changed = false
  for (const { key } of utmFields) {
    const replacement = fields[key]?.trim()
    const existing = url.searchParams.getAll(key)
    // Blank fields retain the first existing value; set also removes duplicates.
    if (replacement || existing.length > 1) {
      url.searchParams.set(key, replacement || existing[0])
      changed = true
    }
  }
  const result = changed ? url.href : value.trim()
  return parseDestination(result) ? result : null
}
