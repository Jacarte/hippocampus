# mem0-functional plugin architecture

This document explains the behavior and design of the maintained `mem0.ts`
OpenCode plugin template. See the [README](./README.md) for copy installation.

## TL;DR

The plugin injects a bounded set of durable memories only when a turn is likely
to benefit from them. It also exposes `mem0` modes for `add`, `search`, `list`,
`forget`, and `help`, and can add project memory to session compaction.
Automatic recall, explicit search, and compaction call `POST /retrieve`.
Supersession detection during `add` alone calls `POST /search`. Requests are
retried and guarded by a circuit breaker, while retrieval failures leave chat
available and may reuse the last good chat context.

## OpenCode integration surfaces

The plugin returns seven OpenCode surfaces:

- `chat.message` detects remember/recall signals, first-turn and periodic
  refreshes, and topic shifts. It ranks and deduplicates results before adding
  a bounded synthetic `[MEM0 CONTEXT]` part.
- `tool.mem0` exposes the five explicit modes. `add` may include `anchor` or
  `anchorContext`, and safe Git context may be inferred when available.
- `chat.params` records provider parameter diagnostics only when prompt debug
  logging is enabled; it does not change the parameters.
- `experimental.chat.messages.transform` records summarized message diagnostics
  when prompt debug logging is enabled; it does not transform the messages.
- `experimental.chat.system.transform` records system-prompt diagnostics when
  prompt debug logging is enabled; it does not transform the system prompt.
- `experimental.session.compacting` retrieves up to eight project memories and
  either appends a context block or replaces the compaction prompt.
- `event` archives a completed compaction summary as cold memory when enabled
  and removes session-local state after `session.deleted`.

In practice, `chat.message` decides when automatic retrieval is worthwhile,
and `tool.mem0` handles explicit operations. Compaction and event handling keep
durable context available across long sessions without injecting raw history.

## Server contract

The plugin uses `POST /memories`, `POST /retrieve`, `GET /memories`, and
`DELETE /memories/{memory_id}` for its main memory operations. Automatic recall,
explicit search, and compaction use `POST /retrieve`. During `add`, supersession
detection uses `POST /search` to find similar older memories; this is the only
plugin flow that calls `/search`. The Hippocampus server also offers get-by-ID,
update, history, delete-all, reset, query, and configure operations, but this
plugin does not expose or call those extra operations.

## Why this plugin exists

OpenCode sessions are excellent at short-term reasoning, but they need help with durable continuity across sessions.

This plugin adds a practical memory model:

- Tier 1 (Core): static guidance from `AGENTS.md` (outside this plugin)
- Tier 2 (Working): session-local, temporary memory control in plugin state
- Tier 3 (Long-term): searchable mem0 persistence

The core principle is: **inject less, retrieve smarter, persist only high-signal facts** the implementation of the mem0 plugin is inspired by the opencode-supermemory plugin.

## Why these design choices

### 1) Session working state (Tier 2)

The plugin keeps session-local state (`SessionState`) to avoid repeated noisy injections and to adapt retrieval timing.

Why:

- Without working state, memory injection repeats and token cost grows quickly.
- Session-local state lets us detect topic shifts and refresh only when useful.
- It keeps temporary coordination data out of long-term memory.

Key fields and why they matter:

- `turn`: lets refresh policy be deterministic
- `lastInjectionTurn`: prevents over-injection
- `topicSignature`: enables topic-shift detection
- `injectedMemoryIdsLRU`: suppresses repeated memories
- `workingSet`: tracks the current selected context
- `lastGoodContextSnippet`: fallback when mem0 is temporarily unavailable

### 2) Triggered retrieval, not constant retrieval

Retrieval is triggered on:

- first turn (optional)
- explicit recall intent
- periodic refresh (every N turns)
- topic shift

Why:

- Always retrieving on every turn is expensive and noisy.
- Never refreshing causes stale context in longer sessions.
- Triggered policy balances relevance and cost.

### 3) Cross-scope ranking + dedupe before injection

Candidates from user/project/agent/environment are pooled and ranked, then deduped.

Why:

- Scope-by-scope injection can over-represent one scope and duplicate facts.
- A unified ranking chooses globally best memories for this turn.
- Dedupe protects context quality and reduces prompt bloat.

Current weighting intention:

- semantic relevance is primary
- recency prevents stale dominance
- type weight favors durable facts/decisions
- scope boost gives project context mild priority

### 4) Hard injection budget

The plugin enforces char/token-like bounds and item count limits.

Why:

- Unlimited context growth degrades model quality and cost.
- Hard budgets make behavior predictable and tunable.

### 5) Lifecycle metadata on writes (Tier 3)

Writes include metadata such as `created_at`, `last_used_at`, `access_count`, `fingerprint`, `decay_half_life_days`, and `tier`.

Why:

- Long-term memory without lifecycle becomes stale and noisy.
- Metadata enables better ranking and future cleanup.
- Fingerprints support dedupe and supersession workflows.

### 6) Supersession detection

New memories can mark similar old entries as superseded.

Why:

- Decisions evolve; old decisions should not be equally ranked forever.
- Supersession keeps history while reducing retrieval confusion.

### 7) Circuit breaker + retries + fallback

The plugin retries mem0 calls, then opens a circuit breaker after repeated failures, and can inject last-known-good context.

Why:

- Memory backend instability should never break chat flow.
- Retries handle transient network errors.
- Breaker avoids repeated expensive failures.
- Fallback maintains continuity during outages.

### 8) Optional cold compaction archival

Compaction summaries can be stored as cold context (`inject: false`).

Why:

- Full summaries are useful for audit/recovery.
- They are usually too broad for default prompt injection.
- Keeping them cold preserves recall without polluting active context.

## Why high-signal-only persistence

The plugin intentionally stores only:

- decisions
- problems + fixes
- stable facts
- reusable procedures

Why:

- Raw transcripts degrade retrieval precision.
- High-signal filtering keeps memory useful over time.
- It reduces storage and retrieval cost.

## Tweakable environment variables

This plugin is almost entirely tuned through environment variables. The list below reflects what the code actually reads today.

### Server connection and retrieval

- `MEM0_SERVER_URL`
  - Source fallback: `http://100.75.83.103:18000`
  - What it does: points the plugin at the mem0-compatible backend and strips any trailing `/` characters.
  - Important: the fallback is an environment-specific address, not a portable localhost default. Set this variable explicitly to the running server's base URL.

Automatic recall sends one `POST /retrieve` request with the user, project,
agent, and environment scopes, the derived identifiers, `limit: 10`, and
`filters.include_cold_context: false`. Explicit `search` calls the same endpoint
for its selected scope. Retrieve responses preserve normalized
`backend_capabilities`, `degraded`, `degradation_reasons`, and `request_id`
diagnostics when the server supplies them.

Supersession detection is deliberately different. When `add` checks for
similar older memories, `detectSupersedes` uses the server's scope-specific
`POST /search` API. The current server API and plugin implementation require
that exception; no recall, explicit-search, or compaction flow uses `/search`.

- `MEM0_READ_TIMEOUT_MS`
  - Default: `8000`
  - What it does: timeout for the supersession-detection `POST /search` request
    and all `GET` requests.
  - Tradeoff: lower values fail faster; higher values tolerate slower backends.

- `MEM0_WRITE_TIMEOUT_MS`
  - Default: `45000`
  - What it does: timeout for all other requests, including `POST /memories`, `POST /retrieve`, and deletes. Despite being a retrieval endpoint, `/retrieve` currently uses this timeout.
  - Tradeoff: higher values are safer for slow persistence paths, but failures take longer to surface.

### Retrieval cadence and prompt-budget controls

- `MEM0_REFRESH_EVERY_TURNS`
  - Default: `10`
  - What it does: if memory was previously injected, forces a periodic refresh after this many turns.
  - Tradeoff: lower means fresher context but more retrieval overhead.

- `MEM0_AUTO_RETRIEVE_FIRST_TURN`
  - Default: enabled
  - Disable with: `0`
  - What it does: controls whether the first user turn is allowed to trigger automatic retrieval.
  - Tradeoff: disabling it reduces first-turn noise, but can delay useful context injection.

- `MEM0_MAX_INJECT_CHARS`
  - Default: `2200`
  - What it does: hard cap for the total injected `[MEM0 CONTEXT]` block.
  - Tradeoff: lower values reduce prompt cost; higher values preserve more retrieved memory.

- `MEM0_MAX_RECENT_IDS`
  - Default: `40`
  - What it does: size of the in-session LRU used to avoid reinjecting the same memories too often.
  - Tradeoff: higher values reduce repetition across longer sessions but may suppress useful repeats longer.

- `MEM0_SIMILARITY_DEDUPE_THRESHOLD`
  - Default: `0.92`
  - What it does: threshold for near-duplicate suppression during injection selection.
  - Tradeoff: lower values dedupe more aggressively; higher values allow more similar memories through.

- `MEM0_SUPERSEDES_THRESHOLD`
  - Default: `0.88`
  - What it does: threshold used when deciding whether a newly saved memory supersedes older similar ones.
  - Tradeoff: lower values create supersession links more often; higher values are more conservative.

### Automatic anchoring and identity scoping

- `MEM0_AUTO_ANCHOR_CONTEXT`
  - Default: `safe`
  - Disable with: `off` or `0`
  - What it does: enables best-effort automatic Git-derived anchor context for writes when enough repository metadata is available.
  - Notes: this uses repo/commit/ref information and only activates when the plugin can safely infer it.

- `MEM0_USER_ID`
  - Default: unset
  - What it does: explicit stable user identifier for memory scoping.
  - Priority: preferred over `OPENCODE_USER_ID`.

- `OPENCODE_USER_ID`
  - Default: unset
  - What it does: fallback explicit user identifier when `MEM0_USER_ID` is not set.

- `USER` / `USERNAME`
  - Default: inherited from the shell/OS if present
  - What they do: last-resort inputs for deriving a stable fallback user identity when neither `MEM0_USER_ID` nor `OPENCODE_USER_ID` is set.
  - Important: these are not plugin-specific knobs, but they do affect identity fallback behavior.

### Compaction behavior

- `MEM0_COMPACTION_MODE`
  - Default: `append`
  - Accepted values: `replace` or anything else, which behaves as `append`
  - What it does: retrieves project memory with the fixed query `recent decisions fixes constraints procedures` and controls whether that memory is appended to context or placed in a replacement prompt.
  - Use `replace` when: you want deterministic compaction output that always includes the Mem0 memory section.

Compaction uses identifiers derived from the active session directory and
sends one `POST /retrieve` request for at most eight results. The request body
uses `scopes: ["project"]`, `limit: 8`, and
`filters.include_cold_context: false`.

On success, append mode adds a memory block only when normalized memories exist
and otherwise changes nothing. Replace mode always sets the Mem0-aware prompt,
including a no-verified-memory statement when the result is empty. After an
exhausted retrieval failure, append mode preserves both existing context and
prompt; replace mode sets the same no-verified-memory fallback prompt.

- `MEM0_SAVE_COLD_COMPACTION`
  - Default: disabled
  - Enable with: `1`
  - What it does: after compaction, archives the generated summary back into mem0 as cold context with `inject: false`.
  - Why this matters: it preserves long-session summaries for later retrieval without automatically polluting prompt injection.

- `MEM0_COLD_MAX_CHARS`
  - Default: `6000`
  - What it does: max size of the stored compaction summary before it is truncated for cold archival.
  - Tradeoff: higher values preserve more of the summary but store more broad context.

### Observability and debug output

- `MEM0_LOG_INJECTION`
  - Default: disabled
  - Enable with: `1`
  - What it does: emits injection-related lifecycle/debug events, including app log writes and NDJSON debug entries.
  - Use this when: you need operational visibility into when and why memory was injected.

- `MEM0_LOG_INJECTION_CONTENT`
  - Default: disabled
  - Enable with: `1`
  - What it does: includes a preview of injected content in logs.
  - Tradeoff: useful for debugging, but increases verbosity and may expose more prompt content than you want.

- `MEM0_DEBUG_PROMPTS`
  - Default: disabled
  - Enable with: `1`
  - What it does: turns on prompt/debug-file lifecycle logging even if normal injection logging is off.

- `MEM0_DEBUG_PROMPT_CONTENT`
  - Default: disabled
  - Enable with: `1`
  - What it does: includes sanitized prompt text in debug output.
  - Notes: content is passed through the plugin's private-content redaction before logging and then truncated by `MEM0_DEBUG_MAX_CHARS`.

- `MEM0_DEBUG_LOG_PATH`
  - Default: `/tmp/opencode-mem0.ndjson`
  - What it does: file path for NDJSON debug events written by the plugin.
  - Change this when: you want logs somewhere persistent or project-local.

- `MEM0_DEBUG_MAX_CHARS`
  - Default: `4000`
  - What it does: upper bound for debug-text payloads written to the NDJSON log.
  - Tradeoff: lower values reduce leakage and file size; higher values preserve more evidence for debugging.

### Backend failure tolerance

- `MEM0_BREAKER_THRESHOLD`
  - Default: `3`
  - What it does: number of failed mem0 request cycles before the circuit breaker opens.
  - Tradeoff: lower values stop repeated failures sooner; higher values retry longer before backing off.

- `MEM0_BREAKER_COOLDOWN_MS`
  - Default: `20000`
  - What it does: how long the breaker stays open before requests are allowed again.
  - Tradeoff: higher values protect the backend more; lower values retry recovery sooner.

### Practical tuning guidance

- If context feels stale, reduce `MEM0_REFRESH_EVERY_TURNS` or keep first-turn retrieval enabled.
- If prompt cost or noise is too high, lower `MEM0_MAX_INJECT_CHARS` and/or lower `MEM0_SIMILARITY_DEDUPE_THRESHOLD` to dedupe harder.
- If your backend is slow or flaky, increase timeouts and/or tune `MEM0_BREAKER_THRESHOLD` plus `MEM0_BREAKER_COOLDOWN_MS`.
- If you are validating behavior, turn on `MEM0_LOG_INJECTION` first; only enable content logging when you actually need payload-level evidence.

## Failure behavior (intentional)

When mem0 is unavailable:

1. the plugin attempts each request up to three times
2. the circuit breaker may open temporarily after failed request cycles
3. chat continues without a memory-backend exception
4. automatic chat retrieval may inject the session's last good context snippet
5. explicit tool modes return a structured failure instead of throwing to chat
6. compaction follows the append/replace failure behavior described above

Why:

- Availability of conversation flow is prioritized over memory freshness.

## Evolution path

Near-term improvements should prioritize:

1. better confidence-based retrieval gating
2. stronger supersession semantics (active/inactive memory views)
3. periodic lifecycle maintenance (demotion/expiry)
4. evaluation of memory precision and recall quality

Why:

- These improve memory quality without increasing prompt size.
