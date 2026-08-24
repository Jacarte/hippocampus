# mem0-functional Plugin Architecture (Why-First)

This document explains why the plugin is designed the way it is, not just what each function does.

File: `~/.config/opencode/plugins/mem0-functional.ts`

## Reference backend implementation

This plugin is wired to a local reference server implementation at:

- `~/.config/opencode/plugins/mem0server/`
- Server docs [here](https://github.com/Jacarte/smallmem0server/blob/main/README.md)

Runtime endpoint contract expected by this plugin:

- `POST /memories`
- `POST /search`
- `GET /memories`
- `DELETE /memories/{memory_id}`

By default, the plugin resolves `MEM0_SERVER_URL` to `http://192.168.0.160:18000`.

## Hook integration summary

The plugin integrates with OpenCode through four hooks that map to different phases of a session:

- `chat.message`: runs on each user message. It detects recall intent or topic shift, retrieves ranked memories from mem0, and injects a bounded `[MEM0 CONTEXT]` block into the prompt parts.
- `tool` (custom tool `mem0`): provides explicit memory operations (`add`, `search`, `list`, `forget`, `help`) so the assistant can persist and query high-signal memories on demand.
- `tool` `add` mode can optionally pass backend-owned anchoring data via `anchor` or `anchorContext`, which the mem0 server validates or derives into canonical anchor metadata.
- `experimental.session.compacting`: runs during session compaction and appends high-signal project memory (or replaces compaction prompt when configured) so durable context survives long sessions.
- `event`: listens to runtime events such as `message.updated` (to archive finished compaction summaries as cold memory) and `session.deleted` (to clean per-session in-memory state).

Operational flow in practice:

1. User sends a message -> `chat.message` decides whether retrieval is worth it.
2. If retrieval is triggered, mem0 search results are ranked, deduped, and injected under strict budget limits.
3. If the model decides to store/retrieve explicitly, it calls the `mem0` tool through the plugin's custom tool hook.
4. On compaction, `experimental.session.compacting` and `event` cooperate to preserve long-term continuity while keeping active context lean.

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

### Backend connection and retrieval mode

- `MEM0_SERVER_URL`
  - Default: `http://192.168.0.160:18000`
  - What it does: points the plugin at the mem0-compatible backend and strips any trailing `/` characters.
  - Change this when: the backend runs on a different host, port, or base URL.

- `MEM0_BACKEND_MODE`
  - Default: `legacy`
  - Accepted values: `backend` enables the newer `/retrieve` path; anything else falls back to `legacy` mode and uses `/search`.
  - What it does: switches retrieval behavior and response expectations.
  - Change this when: your server supports backend-native retrieval and ranking.

- `MEM0_READ_TIMEOUT_MS`
  - Default: `8000`
  - What it does: timeout for read-like operations such as `/search`, `/retrieve`, and `GET` requests.
  - Tradeoff: lower values fail faster; higher values tolerate slower backends.

- `MEM0_WRITE_TIMEOUT_MS`
  - Default: `45000`
  - What it does: timeout for write-like operations such as `POST /memories`.
  - Tradeoff: higher values are safer for slow persistence paths, but failures take longer to surface.

### Retrieval cadence and prompt-budget controls

- `MEM0_REFRESH_EVERY_TURNS`
  - Default: `6`
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
  - What it does: controls whether Mem0 project memory is appended to the compaction prompt or replaces it with a stricter Mem0-aware compaction prompt.
  - Use `replace` when: you want deterministic compaction output that always includes the Mem0 memory section.

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

1. retries are attempted
2. breaker may open temporarily
3. chat keeps running without hard failure
4. fallback snippet may be injected if available

Why:

- Availability of conversation flow is prioritized over memory freshness.

## Evolution path

Near-term improvements should prioritize:

1. better confidence-based retrieval gating
2. stronger supersession semantics (active/inactive memory views)
3. periodic lifecycle maintenance (demotion/expiry)
4. evaluation harness for memory precision/recall quality

Why:

- These improve memory quality without increasing prompt size.
