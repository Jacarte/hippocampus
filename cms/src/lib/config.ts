const DEFAULT_API_BASE_URL = 'http://mem0-server:18000'

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}

export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim()

  if (configured) {
    return normalizeBaseUrl(configured)
  }

  return import.meta.env.DEV ? '' : DEFAULT_API_BASE_URL
}

export function getBackendDisplayUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim()

  return configured ? normalizeBaseUrl(configured) : DEFAULT_API_BASE_URL
}
