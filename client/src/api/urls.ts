import { api } from "@/lib/api"
import type { ListResponse, ShortCodeResponse, Url } from "@/types/api"

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
  return api<Url>("/urls", {
    method: "POST",
    body: JSON.stringify(input),
  })
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
