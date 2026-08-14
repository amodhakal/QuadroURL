import { ApiError, api, API_BASE } from "@/lib/api"
import type {
  CreateUrlResponse,
  ListResponse,
  ShortCodeResponse,
  Url,
  UrlStatus,
} from "@/types/api"

export interface CreateUrlInput {
  user_id: number
  original_url: string
  title: string
}

export interface UpdateUrlInput {
  title?: string
  is_active?: boolean
}

export async function listUrls(params?: {
  size?: number
  offset?: number
}): Promise<ListResponse<Url>> {
  const query = new URLSearchParams()
  if (params?.size !== undefined) query.set("size", String(params.size))
  if (params?.offset !== undefined) query.set("offset", String(params.offset))
  const qs = query.toString()
  return api<ListResponse<Url>>(`/urls${qs ? `?${qs}` : ""}`)
}

export async function createUrl(input: CreateUrlInput): Promise<Url> {
  const created = await api<CreateUrlResponse | Url>("/urls", {
    method: "POST",
    body: JSON.stringify(input),
  })

  if ("id" in created) {
    return created as Url
  }

  // Async two-phase create: poll the status endpoint until the URL is ready.
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const status = await getUrlStatus(created.request_id)
    if (status.status === "ready") {
      return {
        id: status.id,
        user_id: input.user_id,
        short_code: status.short_code,
        original_url: status.original_url,
        title: status.title,
        is_active: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
    }
    if (status.status === "error") {
      throw new ApiError(500, status.error)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new ApiError(504, "Timed out waiting for short link to be created")
}

export async function getUrlStatus(requestId: string): Promise<UrlStatus> {
  const response = await fetch(`${API_BASE}/urls/${requestId}/status`)
  if (response.status === 404 || response.status === 503) {
    return { status: "pending" }
  }
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`
    try {
      const body = await response.json()
      if (typeof body.error === "string" && body.error.length > 0) {
        message = body.error
      }
    } catch {
      // Non-JSON error body; keep the default message.
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as UrlStatus
}

export async function updateUrl(
  id: number,
  input: UpdateUrlInput,
): Promise<Url> {
  return api<Url>(`/urls/${id}`, {
    method: "PUT",
    body: JSON.stringify(input),
  })
}

export async function deleteUrl(id: number): Promise<Record<string, never>> {
  return api<Record<string, never>>(`/urls/${id}`, {
    method: "DELETE",
  })
}

export async function resolveShortCode(
  code: string,
): Promise<ShortCodeResponse> {
  return api<ShortCodeResponse>(`/r/${code}`)
}

export function shortLink(code: string): string {
  const base =
    import.meta.env.VITE_PUBLIC_URL ??
    `${window.location.protocol}//${window.location.hostname}`
  return `${base}/urls/${code}/redirect`
}
