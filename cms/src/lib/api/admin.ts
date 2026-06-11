import { apiClient } from './client.ts'
import type {
  AdminHealthResponse,
  AdminIndexOverviewResponse,
  AdminMemoryDetail,
  AdminMemoryFilters,
  AdminMemoryListResponse,
  CopyAdminMemoryRequest,
  CopyAdminMemoryResponse,
  CreateAdminMemoryRequest,
  CreateAdminMemoryResponse,
  UpdateAdminMemoryRequest,
  VisitMemoryRequest,
  VisitMemoryResponse,
} from './types.ts'

function buildQueryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') {
      search.set(key, String(value))
    }
  }

  const query = search.toString()
  return query ? `?${query}` : ''
}

export const adminApi = {
  getHealth(): Promise<AdminHealthResponse> {
    return apiClient.get('/admin/health')
  },
  listMemories(filters: AdminMemoryFilters): Promise<AdminMemoryListResponse> {
    const query = buildQueryString({
      scope: filters.scope,
      scope_id: filters.scopeId,
      page: filters.page ?? 1,
      page_size: filters.pageSize ?? 20,
      query: filters.query,
    })

    return apiClient.get(`/admin/memories${query}`)
  },
  getMemoryDetail(memoryId: string): Promise<AdminMemoryDetail> {
    return apiClient.get(`/admin/memories/${memoryId}`)
  },
  createMemory(payload: CreateAdminMemoryRequest): Promise<CreateAdminMemoryResponse> {
    return apiClient.post('/admin/memories', payload)
  },
  updateMemory(memoryId: string, payload: UpdateAdminMemoryRequest): Promise<AdminMemoryDetail> {
    return apiClient.put(`/admin/memories/${memoryId}`, payload)
  },
  copyMemory(memoryId: string, payload: CopyAdminMemoryRequest): Promise<CopyAdminMemoryResponse> {
    return apiClient.post(`/admin/memories/${memoryId}/copy`, payload)
  },
  recordVisit(memoryId: string, payload: VisitMemoryRequest): Promise<VisitMemoryResponse> {
    return apiClient.post(`/admin/memories/${memoryId}/visits`, payload)
  },
  deleteMemory(memoryId: string): Promise<void> {
    return apiClient.delete(`/admin/memories/${memoryId}`)
  },
  getIndexOverview(): Promise<AdminIndexOverviewResponse> {
    return apiClient.get('/admin/index/overview')
  },
}
