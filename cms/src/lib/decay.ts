import type { FreshnessSummary } from './api/types.ts'

/** Map memory type to a default half-life in days. */
export function deriveHalfLifeDays(type: string): number {
  if (type === 'decision' || type === 'stable-fact') return 30
  if (type === 'procedure') return 7
  if (type === 'problem-fix') return 4
  return 4
}

/**
 * Compute the recency score for a memory.
 *
 * ageDays = max(0, (nowMs - createdAtMs) / 86400000)
 * recency = 0.5 ** (ageDays / halfLifeDays)
 * if ttlExpiresAt is in the past → recency *= 0.25
 */
export function computeRecencyScore(
  nowMs: number,
  createdAtMs: number | undefined,
  halfLifeDays: number,
  ttlExpiresAtMs: number | undefined,
): number {
  if (!createdAtMs) return 0.5
  const ageDays = Math.max(0, (nowMs - createdAtMs) / 86_400_000)
  const decay = 0.5 ** (ageDays / halfLifeDays)
  const ttlExpired = !!ttlExpiresAtMs && nowMs > ttlExpiresAtMs
  return ttlExpired ? decay * 0.25 : decay
}

/** Return values ready for display on the detail page. */
export type DecayDisplay = {
  halfLifeDays: number
  ageDays: number
  recency: number
  neverVisited: boolean
}

export function computeDecayDisplay(
  freshness: FreshnessSummary | undefined,
  nowMs = Date.now(),
): DecayDisplay {
  if (!freshness) {
    return { halfLifeDays: 30, ageDays: 0, recency: 0.5, neverVisited: true }
  }

  const halfLifeDays = freshness.decay_half_life_days ?? deriveHalfLifeDays('stable-fact')
  const createdAtMs = freshness.created_at ? new Date(freshness.created_at).getTime() : undefined
  const ageDays = createdAtMs !== undefined ? Math.max(0, (nowMs - createdAtMs) / 86_400_000) : 0
  const ttlMs = freshness.ttl_expires_at ? new Date(freshness.ttl_expires_at).getTime() : undefined
  const recency = computeRecencyScore(nowMs, createdAtMs, halfLifeDays, ttlMs)

  return { halfLifeDays, ageDays, recency, neverVisited: freshness.never_visited }
}
