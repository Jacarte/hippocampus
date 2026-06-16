import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
// `setupFiles` is runtime-only, so TS language services do not see the
// `toBeInTheDocument` type augmentation unless the augmenting module is
// imported here directly.
import '@testing-library/jest-dom/vitest'

import { MemoryListShell } from './memory-list-shell.tsx'
import type { AdminMemoryListResponse, AdminMemorySummary } from '../lib/api/types.ts'

// Stub every method on `adminApi`. The render-fidelity test only drives
// `listMemories`, but the component imports the whole surface and Vitest
// throws on undefined method calls, so the rest must be `vi.fn()` too.
vi.mock('../lib/api/admin.ts', () => ({
  adminApi: {
    listMemories: vi.fn(),
    getHealth: vi.fn(),
    listScopes: vi.fn(),
    getMemoryDetail: vi.fn(),
    createMemory: vi.fn(),
    updateMemory: vi.fn(),
    copyMemory: vi.fn(),
    recordVisit: vi.fn(),
    deleteMemory: vi.fn(),
    deleteEmptyMemories: vi.fn(),
    getIndexOverview: vi.fn(),
  },
}))

import { adminApi } from '../lib/api/admin.ts'

const listMemoriesMock = vi.mocked(adminApi.listMemories)

/**
 * Render `MemoryListShell` inside a `MemoryRouter`.
 *
 * The card emits `<Link>` elements for Edit/Decay; a router context is
 * required because react-router-dom throws when a `<Link>` mounts outside a
 * router. Defaults to the `user`/`alice` scope so most tests can call
 * `renderShell()` with no arguments.
 *
 * `onTotalCount` is an opt-in prop: when a test supplies a `vi.fn()`, it is
 * forwarded to the shell so the caller can assert the count callback fires
 * with `response.total_items` from the same mocked list response that
 * renders the visible cards. Tests that do not care about the callback
 * (render-fidelity tests) simply omit the argument and the shell receives
 * `undefined`, matching production usage.
 *
 * @returns The result of `@testing-library/react`'s `render` (a container
 *   plus query helpers). Tests that need to remount (for example to assert
 *   across two scope combinations) destructure `unmount` and call it between
 *   renders — unlike `getQueriesForElement`, `screen` queries are
 *   container-global and do not need to be re-bound after a remount.
 */
function renderShell(
  scope: 'user' | 'agent' | 'run' = 'user',
  scopeId = 'alice',
  onTotalCount?: (count: number) => void,
) {
  return render(
    <MemoryRouter>
      <MemoryListShell scope={scope} scopeId={scopeId} onTotalCount={onTotalCount} />
    </MemoryRouter>,
  )
}

/**
 * Build a single `AdminMemorySummary` with realistic defaults so tests can
 * override only the field under examination.
 *
 * The default scope/content/freshness triplet mirrors what the live backend
 * currently returns for the `user`/`alice` combination and exercises every
 * branch the card renders: a string `metadata.type` (for the type badge),
 * a well-formed `metadata.anchor` (for the anchor snippet), and a non-null
 * `freshness.created_at` (for the formatted date). Tests asserting the
 * null-metadata safe path override `metadata` to `null` and supply their
 * own `freshness.created_at`; the popularity block is included only because
 * the card renders the heat count, not because the tested fields need it.
 *
 * Note: this helper is intentionally NOT shared across test files — keeping
 * the fixture local makes the visible-fields assertion self-contained.
 */
function makeSummary(overrides: Partial<AdminMemorySummary> = {}): AdminMemorySummary {
  return {
    memory_id: 'mem-001',
    scope: 'user',
    scope_id: 'alice',
    content: 'Likes concise answers; dislikes fluff.',
    metadata: {
      type: 'stable-fact',
      anchor: { type: 'file', locator: 'src/middleware/auth.ts#L10-L40' },
    },
    popularity: { total_visits: 12, visit_ratio: 0.42 },
    freshness: {
      last_visited_at: '2024-03-15T10:00:00Z',
      never_visited: false,
      created_at: '2024-03-15T10:00:00Z',
      decay_half_life_days: 30,
      ttl_expires_at: null,
    },
    ...overrides,
  }
}

/**
 * Build a single-page `AdminMemoryListResponse` wrapping the provided
 * `items` array.
 *
 * `page_size` is locked to 20 (the value the component passes in its
 * `AdminMemoryFilters` payload), and `total_pages` is always 1 because the
 * render-fidelity test never exercises pagination — the count-callback and
 * pagination assertions belong to a separate test. Pass an empty `items`
 * array to assert the "no memories" empty-state branch in a future test.
 *
 * The optional `totalItems` override exists so tests that need to
 * distinguish "rows on this page" from "total in the system" (e.g. the
 * count-callback integration test) can hand-build a mismatch. When
 * omitted, `total_items` mirrors `items.length` — the realistic
 * single-page case the render-fidelity tests assert.
 */
function makeListResponse(
  items: AdminMemorySummary[],
  totalItems: number = items.length,
): AdminMemoryListResponse {
  return {
    items,
    page: 1,
    page_size: 20,
    total_items: totalItems,
    total_pages: 1,
  }
}

describe('MemoryListShell render fidelity', () => {
  beforeEach(() => {
    listMemoriesMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders summary content, scope tag, type badge, anchor snippet, and created date from a populated response', async () => {
    const summary = makeSummary({
      memory_id: 'mem-router',
      scope: 'agent',
      scope_id: 'researcher',
      content: 'Uses TanStack Router for all new React projects.',
      metadata: {
        type: 'stable-fact',
        anchor: { type: 'file', locator: 'src/router/index.tsx#L1-L20' },
      },
      freshness: {
        last_visited_at: '2024-03-15T10:00:00Z',
        never_visited: false,
        created_at: '2024-03-15T10:00:00Z',
        decay_half_life_days: 30,
        ttl_expires_at: null,
      },
    })
    listMemoriesMock.mockResolvedValueOnce(makeListResponse([summary]))

    renderShell('agent', 'researcher')

    // Scope tag — `a:` prefix for `agent` scope followed by the scope id.
    const scopeTag = await screen.findByText('a:researcher')
    expect(scopeTag).toBeInTheDocument()

    // Type badge — comes from `metadata.type` when present.
    const typeBadge = screen.getByText('stable-fact')
    expect(typeBadge).toBeInTheDocument()

    // Anchor snippet — emoji + `type:` + locator truncated to 30 chars.
    // The fixture locator is 30 chars long, so it renders in full.
    const anchorSnippet = screen.getByText(/📎\s*file:\s*src\/router\/index\.tsx#L1-L20/)
    expect(anchorSnippet).toBeInTheDocument()

    // Summary content — verbatim text from the API response.
    const content = screen.getByText('Uses TanStack Router for all new React projects.')
    expect(content).toBeInTheDocument()

    // Created date — formatted as `dd/mm/yyyy` via `toLocaleDateString('en-GB', ...)`.
    // jsdom + Node share the ICU behavior for this en-GB format, so the exact
    // string is stable across runs.
    expect(screen.getByText('Created: 15/03/2024')).toBeInTheDocument()

    // Sanity: `listMemories` was called exactly once with the expected scope
    // filter so the test is asserting against the real mocked contract and
    // not a no-op render.
    await waitFor(() => {
      expect(listMemoriesMock).toHaveBeenCalledTimes(1)
    })
    const callArg = listMemoriesMock.mock.calls[0]?.[0]
    expect(callArg?.scope).toBe('agent')
    expect(callArg?.scopeId).toBe('researcher')
  })

  it('renders user scope as `u:` prefix and run scope as `r:` prefix', async () => {
    const userSummary = makeSummary({
      memory_id: 'mem-user',
      scope: 'user',
      scope_id: 'bob',
      content: 'Bob user-scope memory.',
      metadata: { type: 'procedure' },
      freshness: {
        last_visited_at: null,
        never_visited: true,
        created_at: '2024-04-01T08:30:00Z',
        decay_half_life_days: null,
        ttl_expires_at: null,
      },
    })
    const runSummary = makeSummary({
      memory_id: 'mem-run',
      scope: 'run',
      scope_id: 'run-2026-06-10',
      content: 'Run-scope memory.',
      metadata: { type: 'decision' },
      freshness: {
        last_visited_at: null,
        never_visited: true,
        created_at: '2024-05-20T12:00:00Z',
        decay_half_life_days: null,
        ttl_expires_at: null,
      },
    })

    listMemoriesMock
      .mockResolvedValueOnce(makeListResponse([userSummary]))
      .mockResolvedValueOnce(makeListResponse([runSummary]))

    const { unmount } = renderShell('user', 'bob')
    expect(await screen.findByText('u:bob')).toBeInTheDocument()
    expect(screen.getByText('procedure')).toBeInTheDocument()
    expect(screen.getByText('Bob user-scope memory.')).toBeInTheDocument()
    expect(screen.getByText('Created: 01/04/2024')).toBeInTheDocument()

    unmount()

    renderShell('run', 'run-2026-06-10')
    expect(await screen.findByText('r:run-2026-06-10')).toBeInTheDocument()
    expect(screen.getByText('decision')).toBeInTheDocument()
    expect(screen.getByText('Run-scope memory.')).toBeInTheDocument()
    expect(screen.getByText('Created: 20/05/2024')).toBeInTheDocument()
  })

  it('renders a card safely when metadata is null (no type badge, no anchor snippet)', async () => {
    const summary = makeSummary({
      memory_id: 'mem-nullmeta',
      scope: 'user',
      scope_id: 'carol',
      content: 'Memory row with null metadata.',
      metadata: null,
      freshness: {
        last_visited_at: null,
        never_visited: true,
        created_at: '2024-06-10T00:00:00Z',
        decay_half_life_days: null,
        ttl_expires_at: null,
      },
    })
    listMemoriesMock.mockResolvedValueOnce(makeListResponse([summary]))

    renderShell('user', 'carol')

    // Content and scope tag still render from the non-metadata fields.
    const scopeTag = await screen.findByText('u:carol')
    expect(scopeTag).toBeInTheDocument()
    expect(screen.getByText('Memory row with null metadata.')).toBeInTheDocument()
    expect(screen.getByText('Created: 10/06/2024')).toBeInTheDocument()

    // No type badge — `metadata` is null so the optional chain falls through.
    expect(screen.queryByText('stable-fact')).not.toBeInTheDocument()

    // No anchor snippet — paperclip marker only appears when the anchor
    // object is well-formed (`type` + `locator` strings).
    expect(screen.queryByText(/📎/)).not.toBeInTheDocument()
  })
})

/**
 * Locks the count-callback ↔ grid-render integration contract.
 *
 * One `listMemories()` response drives both the visible cards and the
 * `onTotalCount` callback. The test mocks a response whose `items.length`
 * is 2 (so the grid renders) and whose `total_items` is 47 (deliberately
 * different) so the only way the assertion can pass is if the callback
 * reads `response.total_items` and not `response.items.length` or any
 * other derived value.
 */
describe('MemoryListShell count-to-grid integration', () => {
  beforeEach(() => {
    listMemoriesMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls onTotalCount with response.total_items from the same response that renders cards', async () => {
    const onTotalCount = vi.fn()

    const first = makeSummary({
      memory_id: 'mem-count-1',
      scope: 'user',
      scope_id: 'dave',
      content: 'First card on the page.',
      metadata: { type: 'stable-fact' },
      freshness: {
        last_visited_at: '2024-07-01T09:00:00Z',
        never_visited: false,
        created_at: '2024-07-01T09:00:00Z',
        decay_half_life_days: 30,
        ttl_expires_at: null,
      },
    })
    const second = makeSummary({
      memory_id: 'mem-count-2',
      scope: 'user',
      scope_id: 'dave',
      content: 'Second card on the page.',
      metadata: { type: 'procedure' },
      freshness: {
        last_visited_at: null,
        never_visited: true,
        created_at: '2024-07-02T09:00:00Z',
        decay_half_life_days: null,
        ttl_expires_at: null,
      },
    })

    // `totalItems: 47` is deliberately not `items.length` (2) so the
    // callback assertion cannot pass by accident if the component ever
    // wires the count off the wrong field.
    listMemoriesMock.mockResolvedValueOnce(makeListResponse([first, second], 47))

    renderShell('user', 'dave', onTotalCount)

    // Grid side: the same response must render both cards. A regression
    // where the callback fires but `setItems` is skipped (or the effect
    // short-circuits) would leave the grid empty and these lookups
    // would time out. The two cards share the `u:dave` scope tag, so we
    // assert the count via `findAllByText` (which `getByText` rejects as
    // ambiguous) and the unique content/type-badge per card.
    expect(await screen.findAllByText('u:dave')).toHaveLength(2)
    expect(screen.getByText('First card on the page.')).toBeInTheDocument()
    expect(screen.getByText('Second card on the page.')).toBeInTheDocument()
    expect(screen.getByText('stable-fact')).toBeInTheDocument()
    expect(screen.getByText('procedure')).toBeInTheDocument()

    // Callback side: `waitFor` is required because `onTotalCount` is
    // invoked from inside the `useEffect`'s `.then` after the mocked
    // promise resolves, which is asynchronous w.r.t. the initial render.
    await waitFor(() => {
      expect(onTotalCount).toHaveBeenCalledTimes(1)
    })
    expect(onTotalCount).toHaveBeenCalledWith(47)
  })
})
