import { apiClient } from './client.ts'
import type {
  ReadOnlyQueryRequest,
  ReadOnlyRetrieveRequest,
  ReadOnlySearchRequest,
  ScopeSelection,
} from './types.ts'

function toIdentifierParams(selection: ScopeSelection): URLSearchParams {
  const params = new URLSearchParams()

  if (selection.scope === 'user') {
    params.set('user_id', selection.scopeId)
  }

  if (selection.scope === 'agent') {
    params.set('agent_id', selection.scopeId)
  }

  if (selection.scope === 'run') {
    params.set('run_id', selection.scopeId)
  }

  return params
}

export const readOnlyApi = {
  listMemories(selection: ScopeSelection): Promise<unknown> {
    const params = toIdentifierParams(selection)
    return apiClient.get(`/memories?${params.toString()}`)
  },
  search(payload: ReadOnlySearchRequest): Promise<unknown> {
    return apiClient.post('/search', payload)
  },
  retrieve(payload: ReadOnlyRetrieveRequest): Promise<unknown> {
    return apiClient.post('/retrieve', payload)
  },
  query(payload: ReadOnlyQueryRequest): Promise<unknown> {
    return apiClient.post('/query', payload)
  },
  getQueryCapabilities(): Promise<unknown> {
    return apiClient.get('/query/capabilities')
  },
}
