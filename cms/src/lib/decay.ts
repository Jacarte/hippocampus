import type { ReferenceMemoryCard } from './mock-data.ts'

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

/** Parse a "DD/MM/YYYY" mock-date string to epoch ms. */
export function parseCreatedAt(dateStr: string): number {
  const parts = dateStr.split('/')
  if (parts.length !== 3) return Date.now()
  const [day, month, year] = parts.map(Number)
  return new Date(year, month - 1, day).getTime()
}

/** Return values ready for display on the detail page. */
export type DecayDisplay = {
  halfLifeDays: number
  ageDays: number
  recency: number
  neverVisited: boolean
}

export function computeDecayDisplay(memory: ReferenceMemoryCard, nowMs = Date.now()): DecayDisplay {
  const halfLifeDays = memory.decayHalfLifeDays ?? deriveHalfLifeDays(memory.type)
  const createdAtMs = parseCreatedAt(memory.createdAt)
  const ageDays = Math.max(0, (nowMs - createdAtMs) / 86_400_000)
  const ttlMs = memory.ttlExpiresAt ? Date.parse(memory.ttlExpiresAt) : undefined
  const recency = computeRecencyScore(nowMs, createdAtMs, halfLifeDays, ttlMs)
  const neverVisited = !memory.lastVisitedAt

  return { halfLifeDays, ageDays, recency, neverVisited }
}
