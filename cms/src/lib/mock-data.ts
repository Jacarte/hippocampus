export type ShellSelectField = {
  label: string
  value: string
  options: string[]
}

export type ImpersonationSelection = {
  user: string
  agent: string
  project: string
  run: string
}

export type MemoryScopeTag = {
  prefix: 'u' | 'a' | 'p'
  value: string
}

export type ReferenceMemoryCard = {
  id: string
  heat: number
  createdAt: string
  summary: string
  scopeTags: MemoryScopeTag[]
  type: string
  decayHalfLifeDays?: number
  lastVisitedAt?: string
  ttlExpiresAt?: string
}

export const totalReferenceMemoryCount = 60

export const activeImpersonation: ImpersonationSelection = {
  user: 'alice',
  agent: 'planner',
  project: 'acme-web',
  run: 'optional',
}

export function getImpersonationFields(
  selection: ImpersonationSelection,
): ShellSelectField[] {
  return [
    { label: 'Impersonate · User', value: selection.user, options: ['alice', 'bob', 'carol'] },
    { label: 'Agent', value: selection.agent, options: ['planner', 'researcher', 'writer'] },
    { label: 'Project', value: selection.project, options: ['acme-web', 'atlas-cli', 'mem0-cms'] },
    { label: 'Run', value: selection.run, options: ['optional', 'run-2026-06-10', 'run-nightly'] },
  ]
}

export const filterFields: ShellSelectField[] = [
  { label: 'User', value: 'any', options: ['any', 'alice', 'bob', 'carol'] },
  { label: 'Agent', value: 'any', options: ['any', 'planner', 'researcher', 'writer'] },
  { label: 'Project', value: 'any', options: ['any', 'acme-web', 'atlas-cli', 'mem0-cms'] },
  { label: 'Run', value: 'any', options: ['any', 'optional', 'run-2026-06-10', 'run-nightly'] },
]

export const referenceMemories: ReferenceMemoryCard[] = [
  {
    id: 'alice-researcher-router',
    heat: 16,
    createdAt: '30/12/1969',
    summary: 'Uses TanStack Router for all new React projects.',
    scopeTags: [
      { prefix: 'u', value: 'alice' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'alice-researcher-atlas',
    heat: 20,
    createdAt: '29/12/1969',
    summary: 'Owns a Siberian husky named Atlas.',
    scopeTags: [
      { prefix: 'u', value: 'alice' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'alice-researcher-recharts',
    heat: 43,
    createdAt: '26/12/1969',
    summary: 'Asks for recharts when a chart is needed.',
    scopeTags: [
      { prefix: 'u', value: 'alice' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-planner-stack',
    heat: 5,
    createdAt: '23/12/1969',
    summary: 'Favorite stack: Tailwind + shadcn/ui + TanStack Query.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'planner' },
      { prefix: 'p', value: 'atlas-cli' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-researcher-madrid',
    heat: 25,
    createdAt: '22/12/1969',
    summary: 'Lives in Madrid; UTC+1 by default.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-writer-concise',
    heat: 24,
    createdAt: '22/12/1969',
    summary: 'Prefers concise answers; dislikes fluff.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'writer' },
      { prefix: 'p', value: 'acme-web' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-planner-mock-data',
    heat: 41,
    createdAt: '21/12/1969',
    summary: 'Requests mock data before connecting real APIs.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'planner' },
      { prefix: 'p', value: 'atlas-cli' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-researcher-local-context',
    heat: 14,
    createdAt: '20/12/1969',
    summary: 'Likes to persist impersonation context locally.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-researcher-deep-work',
    heat: 15,
    createdAt: '18/12/1969',
    summary: 'Schedules deep-work blocks on Tuesday mornings.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-researcher-gradient',
    heat: 7,
    createdAt: '17/12/1969',
    summary: 'Wants retrieval heat shown as a color gradient.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-writer-recharts',
    heat: 45,
    createdAt: '12/12/1969',
    summary: 'Asks for recharts when a chart is needed.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'writer' },
      { prefix: 'p', value: 'acme-web' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-writer-design',
    heat: 20,
    createdAt: '12/12/1969',
    summary: 'Likes to see design directions before implementation.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'writer' },
      { prefix: 'p', value: 'acme-web' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-planner-french',
    heat: 9,
    createdAt: '10/12/1969',
    summary: 'Reads in French; UI copy in English.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'planner' },
      { prefix: 'p', value: 'atlas-cli' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'carol-researcher-concise',
    heat: 18,
    createdAt: '07/12/1969',
    summary: 'Prefers concise answers; dislikes fluff.',
    scopeTags: [
      { prefix: 'u', value: 'carol' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-writer-decay',
    heat: 4,
    createdAt: '04/12/1969',
    summary: 'Enjoys half-life decay metaphors for memory relevance.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'writer' },
      { prefix: 'p', value: 'acme-web' },
    ],
    type: 'stable-fact',
  },
  {
    id: 'bob-researcher-french',
    heat: 44,
    createdAt: '29/11/1969',
    summary: 'Reads in French; UI copy in English.',
    scopeTags: [
      { prefix: 'u', value: 'bob' },
      { prefix: 'a', value: 'researcher' },
      { prefix: 'p', value: 'mem0-cms' },
    ],
    type: 'stable-fact',
  },
]

export function getHeatColor(heat: number): string {
  if (heat >= 40) {
    return 'var(--color-heat-hot)'
  }

  if (heat >= 20) {
    return 'var(--color-heat-warm)'
  }

  if (heat >= 10) {
    return 'var(--color-heat-cool)'
  }

  return 'var(--color-heat-cold)'
}

export function findReferenceMemory(memoryId?: string): ReferenceMemoryCard {
  return referenceMemories.find((memory) => memory.id === memoryId) ?? referenceMemories[0]
}
