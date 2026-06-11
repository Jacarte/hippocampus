import { getApiBaseUrl } from '../config.ts'

export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

export type ApiClient = {
  get<T>(path: string): Promise<T>
  post<T>(path: string, body?: unknown): Promise<T>
  put<T>(path: string, body?: unknown): Promise<T>
  delete<T>(path: string): Promise<T>
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export function createApiClient(baseUrl = getApiBaseUrl()): ApiClient {
  const normalizedBaseUrl = baseUrl.replace(/\/+$/, '')

  async function request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(`${normalizedBaseUrl}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        ...init.headers,
      },
    })

    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new ApiError(`Request failed for ${path}`, response.status, payload)
    }

    return parseResponse<T>(response)
  }

  return {
    get: <T>(path: string) => request<T>(path, { method: 'GET' }),
    post: <T>(path: string, body?: unknown) =>
      request<T>(path, {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    put: <T>(path: string, body?: unknown) =>
      request<T>(path, {
        method: 'PUT',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  }
}

export const apiClient = createApiClient()
