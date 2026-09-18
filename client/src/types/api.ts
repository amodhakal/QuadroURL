export interface Url {
  id: number
  user_id: number
  short_code: string
  original_url: string
  title: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface User {
  id: number
  username: string
  email: string
  created_at: string
}

export interface ListResponse<T> {
  kind: string
  sample: T[]
}

export interface ShortCodeResponse {
  url: string
  short_code: string
}

export interface CreateUrlResponse {
  request_id: string
  status: "pending"
}

export type UrlStatus =
  | {
      status: "ready"
      id: number
      short_code: string
      original_url: string
      title: string
    }
  | { status: "error"; error: string }
  | { status: "pending" }

export interface SearchHit {
  id: number
  user_id: number
  short_code: string
  original_url: string
  title: string
  score: number
}

export interface SearchResponse {
  kind: "search"
  query: string
  results: SearchHit[]
}

export interface AskResponse {
  answer: string
  model: string
  sources: SearchHit[]
}
