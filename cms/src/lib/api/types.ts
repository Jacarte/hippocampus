export type ScopeKind = 'user' | 'agent' | 'run'

export type ScopeSelection = {
  scope: ScopeKind
  scopeId: string
}

export type MemoryRole = 'user' | 'assistant'

export type MemoryMessage = {
  role: MemoryRole
  content: string
}

export type MemoryVisitReason = 'detail_open' | 'edit_save' | 'copy_source'

export type PaginationInput = {
  page?: number
  pageSize?: number
}

export type AdminMemoryFilters = ScopeSelection &
  PaginationInput & {
    query?: string
  }

export type PopularitySummary = {
  total_visits: number
  visit_ratio: number
}

export type FreshnessSummary = {
  last_visited_at: string | null
  never_visited: boolean
  created_at: string | null
  decay_half_life_days: number | null
  ttl_expires_at: string | null
}

export type AuditSummary = {
  impersonated_by?: string | null
  copied_from?: Record<string, unknown> | null
}

export type AdminMemorySummary = {
  memory_id: string
  scope: ScopeKind
  scope_id: string
  content: string
  metadata: Record<string, unknown>
  popularity?: PopularitySummary
  freshness?: FreshnessSummary
}

export type AdminMemoryDetail = AdminMemorySummary & {
  audit?: AuditSummary
}

export type AdminMemoryListResponse = {
  items: AdminMemorySummary[]
  page: number
  page_size: number
  total_items: number
  total_pages: number
}

export type CreateAdminMemoryRequest = ScopeSelection & {
  messages: MemoryMessage[]
  metadata?: Record<string, unknown>
}

export type CreateAdminMemoryResponse = {
  memory_id: string
  scope: ScopeKind
  scope_id: string
  messages: MemoryMessage[]
  metadata: Record<string, unknown>
  impersonated_by: string
}

export type UpdateAdminMemoryRequest = {
  messages: MemoryMessage[]
  metadata?: Record<string, unknown>
}

export type CopyAdminMemoryRequest = {
  target_scope: ScopeKind
  target_scope_id: string
}

export type VisitMemoryRequest = {
  reason: MemoryVisitReason
}

export type CopyAdminMemoryResponse = {
  source_memory_id: string
  target_memory_id: string
  target_scope: ScopeKind
  target_scope_id: string
  copied_from: Record<string, unknown>
  impersonated_by: string
}

export type VisitMemoryResponse = {
  memory_id: string
  total_visits: number
  last_visited_at: string | null
  reason: MemoryVisitReason
}

export type AdminHealthResponse = Record<string, unknown>

export type AdminIndexOverviewResponse = {
  roots: Array<{
    root: string
    total_files: number
    total_chunks: number
    watcher_active: boolean
    last_job_id: string | null
  }>
  jobs: Array<{
    job_id: string
    status: string
    queued_at: string | null
    started_at: string | null
    completed_at: string | null
    result: unknown
    errors: unknown[]
  }>
  files: Array<{
    root: string
    file_path: string
    chunk_count: number
    language: string | null
    last_indexed_at: string | null
    has_summary_embedding: boolean
  }>
  limits: {
    current_process_state_only: boolean
  }
  visibility_inputs: {
    generated_at: string
    root_count: number
    file_count: number
    chunk_count: number
  }
}

export type ReadOnlyQueryRequest = {
  query: string
  corpora?: string[]
  limit?: number
  path_filter?: string
  language_filter?: string
}

export type ReadOnlySearchRequest = {
  query: string
  user_id?: string
  agent_id?: string
  run_id?: string
  filters?: Record<string, unknown>
}

export type ReadOnlyRetrieveRequest = {
  query: string
  user_id?: string
  agent_id?: string
  run_id?: string
  limit?: number
}
