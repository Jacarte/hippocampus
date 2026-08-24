import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import type { Part } from "@opencode-ai/sdk";
import { tool, type Plugin } from "@opencode-ai/plugin";

// URL of the Mem0 REST API backend
const MEM0_SERVER_URL = (
	process.env.MEM0_SERVER_URL || "http://100.75.83.103:18000"
).replace(/\/+$/, "");
// Auto-anchor context inference: "off" or "safe"
const MEM0_AUTO_ANCHOR_CONTEXT = resolveAutoAnchorContextMode(
	process.env.MEM0_AUTO_ANCHOR_CONTEXT,
);
// Storage compaction strategy: "replace" overwrites, "append" adds to existing
const MEM0_COMPACTION_MODE =
	process.env.MEM0_COMPACTION_MODE === "replace" ? "replace" : "append";
// Enable cold compaction when saving memories
const MEM0_SAVE_COLD_COMPACTION = process.env.MEM0_SAVE_COLD_COMPACTION === "1";
// Log injected memory context to debug log
const MEM0_LOG_INJECTION = process.env.MEM0_LOG_INJECTION === "1";
// Include full memory content in injection logs
const MEM0_LOG_INJECTION_CONTENT =
	process.env.MEM0_LOG_INJECTION_CONTENT === "1";
// Log prompts sent to Mem0 backend
const MEM0_DEBUG_PROMPTS = process.env.MEM0_DEBUG_PROMPTS === "1";
// Include full prompt content in debug logs
const MEM0_DEBUG_PROMPT_CONTENT = process.env.MEM0_DEBUG_PROMPT_CONTENT === "1";
// File path for mem0 debug log output
const MEM0_DEBUG_LOG_PATH =
	process.env.MEM0_DEBUG_LOG_PATH || "/tmp/opencode-mem0.ndjson";
// Max characters per entry in debug logs
const MEM0_DEBUG_MAX_CHARS = Number(process.env.MEM0_DEBUG_MAX_CHARS || 4000);
// Max characters for cold-compacted memory content
const MEM0_COLD_MAX_CHARS = Number(process.env.MEM0_COLD_MAX_CHARS || 6000);
const MEM0_API_MAX_ATTEMPTS = 3;
const MEM0_API_RETRY_DELAY_MS = 250;
// Refresh memories every N conversation turns
const MEM0_REFRESH_EVERY_TURNS = Number(
	process.env.MEM0_REFRESH_EVERY_TURNS || 10,
);
// Auto-retrieve memories on first turn; set to "0" to disable
const MEM0_AUTO_RETRIEVE_FIRST_TURN =
	process.env.MEM0_AUTO_RETRIEVE_FIRST_TURN !== "0";
// Max characters to inject into conversation context
const MEM0_MAX_INJECT_CHARS = Number(process.env.MEM0_MAX_INJECT_CHARS || 2200);
// Max recent memory IDs to track
const MEM0_MAX_RECENT_IDS = Number(process.env.MEM0_MAX_RECENT_IDS || 40);
// Similarity threshold for deduplicating memories (0-1)
const MEM0_SIMILARITY_DEDUPE_THRESHOLD = Number(
	process.env.MEM0_SIMILARITY_DEDUPE_THRESHOLD || 0.92,
);
// Similarity threshold for superseding old memories (0-1)
const MEM0_SUPERSEDES_THRESHOLD = Number(
	process.env.MEM0_SUPERSEDES_THRESHOLD || 0.88,
);
// Consecutive failures before circuit-breaker opens
const MEM0_BREAKER_THRESHOLD = Number(process.env.MEM0_BREAKER_THRESHOLD || 3);
// Circuit-breaker cooldown duration in milliseconds
const MEM0_BREAKER_COOLDOWN_MS = Number(
	process.env.MEM0_BREAKER_COOLDOWN_MS || 20_000,
);
// HTTP timeout for read operations in milliseconds
const MEM0_READ_TIMEOUT_MS = Number(process.env.MEM0_READ_TIMEOUT_MS || 8_000);
// HTTP timeout for write operations in milliseconds
const MEM0_WRITE_TIMEOUT_MS = Number(
	process.env.MEM0_WRITE_TIMEOUT_MS || 45_000,
);
const MAX_CONTEXT_ITEMS = 10;

type Scope = "user" | "project" | "agent" | "environment";
type MemoryType =
	| "decision"
	| "problem-fix"
	| "stable-fact"
	| "procedure"
	| "noise";
type Mem0AutoAnchorContextMode = "off" | "safe";
type Mem0AnchorType = "file" | "commit" | "pr" | "issue";
type Mem0AnchorProvenanceMode = "verified" | "derived";
type JsonRecord = Record<string, unknown>;

type GitCommandRunner = (
	directory: string,
	args: string[],
) => string | undefined;

interface InferAnchorContextOptions {
	enabled?: boolean;
	env?: NodeJS.ProcessEnv;
	runGit?: GitCommandRunner;
}

interface Mem0AnchorProvenance {
	mode: Mem0AnchorProvenanceMode;
	source: string;
	commit_pinned: boolean;
}

interface Mem0Anchor {
	type: Mem0AnchorType;
	repo: string;
	locator: string;
	ref?: string;
	commit_sha?: string;
	url?: string;
	title?: string;
	created_at: string;
	observed_at?: string;
	is_stale: boolean;
	provenance: Mem0AnchorProvenance;
}

interface Mem0AnchorContext {
	type?: Mem0AnchorType;
	repo?: string;
	locator?: string;
	path?: string;
	file_path?: string;
	ref?: string;
	commit_sha?: string;
	url?: string;
	title?: string;
	created_at?: string;
	observed_at?: string;
	is_stale?: boolean;
	pr_number?: string;
	issue_number?: string;
}

interface Mem0ScopeIdentifiers {
	user_id: string;
	agent_id: string;
	run_id?: string;
}

interface MemoryResult {
	id: string;
	content: string;
	score?: number;
	anchor?: JsonRecord | null;
	metadata?: JsonRecord;
}

interface MemoryCandidate extends MemoryResult {
	scope: Scope;
	type: string;
	fingerprint: string;
	inject: boolean;
}

interface SessionState {
	turn: number;
	lastInjectionTurn: number;
	topicSignature: string;
	injectedMemoryIdsLRU: string[];
	workingSet: MemoryCandidate[];
	lastGoodContextSnippet: string;
}

interface PluginMetrics {
	retrievalAttempts: number;
	retrievalHits: number;
	dedupeDrops: number;
	budgetDrops: number;
	breakerOpens: number;
}

interface Mem0BackendClientDependencies {
	parseResults: (payload: unknown) => MemoryResult[];
	request: (path: string, init: RequestInit) => Promise<unknown>;
}

interface Mem0ToolSearchParams {
	identifiers: Mem0ScopeIdentifiers;
	limit?: number;
	query: string;
	scope: Scope;
	scopes?: Scope[];
}

interface Mem0ToolSearchResponse {
	endpoint: "/retrieve";
	results: MemoryResult[];
	payload: unknown;
	backendCapabilities?: JsonRecord;
	degraded?: boolean;
	degradationReasons?: string[];
	requestId?: string;
}

const mem0AnchorTypeSchema = tool.schema.enum([
	"file",
	"commit",
	"pr",
	"issue",
]);
const mem0AnchorProvenanceSchema = tool.schema.object({
	mode: tool.schema.enum(["verified", "derived"]),
	source: tool.schema.string(),
	commit_pinned: tool.schema.boolean(),
});
const mem0AnchorSchema = tool.schema.object({
	type: mem0AnchorTypeSchema,
	repo: tool.schema.string(),
	locator: tool.schema.string(),
	ref: tool.schema.string().optional(),
	commit_sha: tool.schema.string().optional(),
	url: tool.schema.string().optional(),
	title: tool.schema.string().optional(),
	created_at: tool.schema.string(),
	observed_at: tool.schema.string().optional(),
	is_stale: tool.schema.boolean(),
	provenance: mem0AnchorProvenanceSchema,
});
const mem0AnchorContextSchema = tool.schema.object({
	type: mem0AnchorTypeSchema.optional(),
	repo: tool.schema.string().optional(),
	locator: tool.schema.string().optional(),
	path: tool.schema.string().optional(),
	file_path: tool.schema.string().optional(),
	ref: tool.schema.string().optional(),
	commit_sha: tool.schema.string().optional(),
	url: tool.schema.string().optional(),
	title: tool.schema.string().optional(),
	created_at: tool.schema.string().optional(),
	observed_at: tool.schema.string().optional(),
	is_stale: tool.schema.boolean().optional(),
	pr_number: tool.schema.string().optional(),
	issue_number: tool.schema.string().optional(),
});

function asRecord(value: unknown): JsonRecord | null {
	return value && typeof value === "object" ? (value as JsonRecord) : null;
}

function omitUndefinedDeep(value: unknown): unknown {
	if (Array.isArray(value)) {
		return value.map((entry) => omitUndefinedDeep(entry));
	}

	const record = asRecord(value);
	if (!record) return value;

	const cleaned: JsonRecord = {};
	for (const [key, entry] of Object.entries(record)) {
		if (entry === undefined) continue;
		cleaned[key] = omitUndefinedDeep(entry);
	}
	return cleaned;
}

function normalizeAnchorWriteInput(
	anchor: Mem0Anchor | undefined,
): JsonRecord | undefined {
	if (!anchor) return undefined;
	return asRecord(omitUndefinedDeep(anchor)) || undefined;
}

function normalizeAnchorContextWriteInput(
	anchorContext: Mem0AnchorContext | undefined,
): JsonRecord | undefined {
	if (!anchorContext) return undefined;
	return asRecord(omitUndefinedDeep(anchorContext)) || undefined;
}

function asStringList(value: unknown): string[] | undefined {
	if (!Array.isArray(value)) return undefined;
	const entries = value.filter(
		(entry): entry is string => typeof entry === "string" && entry.length > 0,
	);
	return entries.length > 0 ? entries : undefined;
}

function normalizeLimit(value: number | undefined): number {
	if (!Number.isFinite(value)) return 10;
	return Math.max(1, Math.floor(value || 10));
}

function resolveAutoAnchorContextMode(
	value: string | undefined,
): Mem0AutoAnchorContextMode {
	return value === "0" || value === "off" ? "off" : "safe";
}

function inferAnchorContextType(
	anchorContext: Mem0AnchorContext | undefined,
): Mem0AnchorContext | undefined {
	if (!anchorContext) return undefined;
	if (anchorContext.type) return anchorContext;

	if (anchorContext.locator || anchorContext.path || anchorContext.file_path) {
		return { ...anchorContext, type: "file" };
	}
	if (anchorContext.pr_number) {
		return { ...anchorContext, type: "pr" };
	}
	if (anchorContext.issue_number) {
		return { ...anchorContext, type: "issue" };
	}
	if (anchorContext.commit_sha) {
		return { ...anchorContext, type: "commit" };
	}

	return anchorContext;
}

function mergeAnchorContexts(
	inferred: Mem0AnchorContext | undefined,
	explicit: Mem0AnchorContext | undefined,
): Mem0AnchorContext | undefined {
	const normalizedExplicit = inferAnchorContextType(explicit);
	if (!inferred) return normalizedExplicit;
	if (!normalizedExplicit) return inferred;

	const merged: Mem0AnchorContext = {
		...inferred,
		...(normalizedExplicit.type !== undefined
			? { type: normalizedExplicit.type }
			: {}),
		...(normalizedExplicit.repo !== undefined
			? { repo: normalizedExplicit.repo }
			: {}),
		...(normalizedExplicit.locator !== undefined
			? { locator: normalizedExplicit.locator }
			: {}),
		...(normalizedExplicit.path !== undefined
			? { path: normalizedExplicit.path }
			: {}),
		...(normalizedExplicit.file_path !== undefined
			? { file_path: normalizedExplicit.file_path }
			: {}),
		...(normalizedExplicit.ref !== undefined
			? { ref: normalizedExplicit.ref }
			: {}),
		...(normalizedExplicit.commit_sha !== undefined
			? { commit_sha: normalizedExplicit.commit_sha }
			: {}),
		...(normalizedExplicit.url !== undefined
			? { url: normalizedExplicit.url }
			: {}),
		...(normalizedExplicit.title !== undefined
			? { title: normalizedExplicit.title }
			: {}),
		...(normalizedExplicit.created_at !== undefined
			? { created_at: normalizedExplicit.created_at }
			: {}),
		...(normalizedExplicit.observed_at !== undefined
			? { observed_at: normalizedExplicit.observed_at }
			: {}),
		...(normalizedExplicit.is_stale !== undefined
			? { is_stale: normalizedExplicit.is_stale }
			: {}),
		...(normalizedExplicit.pr_number !== undefined
			? { pr_number: normalizedExplicit.pr_number }
			: {}),
		...(normalizedExplicit.issue_number !== undefined
			? { issue_number: normalizedExplicit.issue_number }
			: {}),
	};

	return inferAnchorContextType(merged);
}

function runGitCommand(directory: string, args: string[]): string | undefined {
	try {
		const output = execFileSync("git", ["-C", directory, ...args], {
			encoding: "utf8",
			stdio: ["ignore", "pipe", "ignore"],
		}).trim();
		return output || undefined;
	} catch {
		return undefined;
	}
}

function normalizeGitRepoIdentifier(
	value: string | undefined,
): string | undefined {
	if (!value) return undefined;
	const trimmed = value.trim();
	if (!trimmed) return undefined;

	const withoutGitSuffix = trimmed.replace(/\.git$/i, "");
	const sshMatch = withoutGitSuffix.match(/^git@([^:]+):(.+)$/);
	if (sshMatch) {
		return `${sshMatch[1]}/${sshMatch[2].replace(/^\/+/, "")}`;
	}

	if (
		/^https?:\/\//i.test(withoutGitSuffix) ||
		/^ssh:\/\//i.test(withoutGitSuffix)
	) {
		try {
			const parsed = new URL(withoutGitSuffix);
			const pathname = parsed.pathname.replace(/^\/+/, "");
			return pathname ? `${parsed.host}/${pathname}` : undefined;
		} catch {
			return undefined;
		}
	}

	return withoutGitSuffix.replace(/^\/+/, "") || undefined;
}

function repoFromEnvironment(env: NodeJS.ProcessEnv): string | undefined {
	const githubRepository = env.GITHUB_REPOSITORY?.trim();
	if (githubRepository) {
		const githubHost = normalizeGitRepoIdentifier(
			env.GITHUB_SERVER_URL || "https://github.com",
		);
		const host = githubHost?.split("/")[0] || "github.com";
		return `${host}/${githubRepository}`;
	}

	return normalizeGitRepoIdentifier(env.CI_PROJECT_URL);
}

function refFromEnvironment(env: NodeJS.ProcessEnv): string | undefined {
	const ref =
		env.GITHUB_HEAD_REF || env.GITHUB_REF_NAME || env.CI_COMMIT_REF_NAME;
	if (!ref) return undefined;
	const trimmed = ref.trim();
	return trimmed || undefined;
}

function inferAutomaticAnchorContext(
	directory: string,
	options: InferAnchorContextOptions = {},
): Mem0AnchorContext | undefined {
	const enabled = options.enabled ?? MEM0_AUTO_ANCHOR_CONTEXT === "safe";
	if (!enabled) return undefined;

	const env = options.env ?? process.env;
	const runGit = options.runGit ?? runGitCommand;
	const gitRoot = runGit(directory, ["rev-parse", "--show-toplevel"]);
	if (!gitRoot) return undefined;

	const repo =
		repoFromEnvironment(env) ||
		normalizeGitRepoIdentifier(
			runGit(directory, ["remote", "get-url", "origin"]),
		);
	const commitSHA =
		env.GITHUB_SHA?.trim() ||
		env.CI_COMMIT_SHA?.trim() ||
		runGit(directory, ["rev-parse", "HEAD"]);
	const ref =
		refFromEnvironment(env) ||
		(() => {
			const branch = runGit(directory, ["rev-parse", "--abbrev-ref", "HEAD"]);
			return branch && branch !== "HEAD" ? branch : undefined;
		})();

	if (!repo || !commitSHA) return undefined;

	return {
		type: "commit",
		repo,
		ref,
		commit_sha: commitSHA,
	};
}

function createMem0BackendClient(dependencies: Mem0BackendClientDependencies) {
	return {
		async search(
			params: Mem0ToolSearchParams,
		): Promise<Mem0ToolSearchResponse> {
			const limit = normalizeLimit(params.limit);
			const payload = await dependencies.request("/retrieve", {
				method: "POST",
				body: JSON.stringify({
					query: params.query,
					scopes: params.scopes || [params.scope],
					...params.identifiers,
					limit,
					filters: {
						include_cold_context: false,
					},
				}),
			});

			const parsed = asRecord(payload);
			return {
				endpoint: "/retrieve",
				results: dependencies.parseResults(payload).slice(0, limit),
				payload,
				backendCapabilities:
					asRecord(parsed?.backend_capabilities) || undefined,
				degraded: parsed?.degraded === true,
				degradationReasons: asStringList(parsed?.degradation_reasons),
				requestId:
					typeof parsed?.request_id === "string"
						? parsed.request_id
						: undefined,
			};
		},
	};
}

function textFromParts(parts: unknown): string {
	if (!Array.isArray(parts)) return "";
	return parts
		.map((part) => {
			const row = asRecord(part);
			if (!row || row.type !== "text") return "";
			return typeof row.text === "string" ? row.text : "";
		})
		.filter(Boolean)
		.join("\n")
		.trim();
}

function clampText(text: string, maxChars: number): string {
	if (text.length <= maxChars) return text;
	return `${text.slice(0, maxChars)}\n[TRUNCATED]`;
}

function preview(text: string, maxChars = 220): string {
	return text.length <= maxChars ? text : `${text.slice(0, maxChars)}...`;
}

function shouldWriteDebugFile(): boolean {
	return MEM0_LOG_INJECTION || MEM0_DEBUG_PROMPTS;
}

function sanitizeDebugText(
	text: string,
	maxChars = MEM0_DEBUG_MAX_CHARS,
): string {
	return clampText(stripPrivateContent(text), maxChars);
}

function summarizePart(part: Part): JsonRecord {
	const partInfo = asRecord(part as unknown);
	const synthetic = partInfo?.synthetic === true;

	if (part.type === "text" && typeof part.text === "string") {
		return {
			id: part.id,
			type: part.type,
			synthetic,
			hasMem0Context: part.text.includes("[MEM0 CONTEXT]"),
			text: MEM0_DEBUG_PROMPT_CONTENT
				? sanitizeDebugText(part.text)
				: undefined,
		};
	}

	return {
		id: part.id,
		type: part.type,
		synthetic,
	};
}

function summarizeMessages(
	messages: Array<{ info: unknown; parts: Part[] }>,
): JsonRecord[] {
	return messages.map((message) => {
		const info = asRecord(message.info);
		const combinedText = textFromParts(message.parts);
		return {
			sessionID:
				typeof info?.sessionID === "string" ? info.sessionID : undefined,
			messageID: typeof info?.id === "string" ? info.id : undefined,
			role: typeof info?.role === "string" ? info.role : undefined,
			partCount: message.parts.length,
			hasMem0Context: combinedText.includes("[MEM0 CONTEXT]"),
			parts: message.parts.map(summarizePart),
		};
	});
}

function hasOwnKey(value: JsonRecord, key: string): boolean {
	return Object.prototype.hasOwnProperty.call(value, key);
}

function normalizeAnchor(value: unknown): JsonRecord | null | undefined {
	if (value === null) return null;
	return asRecord(value) || undefined;
}

function compactWhitespace(value: string): string {
	return value.replace(/\s+/g, " ").trim();
}

function shortSha(value: unknown): string | undefined {
	if (typeof value !== "string") return undefined;
	const trimmed = value.trim();
	return trimmed ? trimmed.slice(0, 8) : undefined;
}

function shortRef(value: unknown): string | undefined {
	if (typeof value !== "string") return undefined;
	const trimmed = value.trim();
	if (!trimmed) return undefined;
	return trimmed
		.replace(/^refs\/heads\//, "")
		.replace(/^refs\/tags\//, "")
		.replace(/^refs\/pull\//, "pull/");
}

function extractAnchorNumber(
	locator: unknown,
	prefix: string,
): string | undefined {
	if (typeof locator !== "string") return undefined;
	const match = locator.match(new RegExp(`^${prefix}/(\\d+)$`, "i"));
	return match?.[1];
}

function formatAnchorLabel(
	anchor: JsonRecord | null | undefined,
): string | null {
	if (!anchor) return null;

	const provenance = asRecord(anchor.provenance);
	const mode =
		provenance?.mode === "verified" || provenance?.mode === "derived"
			? provenance.mode
			: "anchor";
	const source =
		typeof provenance?.source === "string" && provenance.source !== "observed"
			? provenance.source
			: undefined;
	const anchorType = typeof anchor.type === "string" ? anchor.type : undefined;
	const title =
		typeof anchor.title === "string"
			? compactWhitespace(anchor.title)
			: undefined;
	const locator =
		typeof anchor.locator === "string" ? anchor.locator : undefined;
	const ref = shortRef(anchor.ref);
	const commit = shortSha(anchor.commit_sha);

	let descriptor: string | null = null;

	if (anchorType === "file") {
		const fileLabel = title || locator;
		if (fileLabel) {
			const extras = [
				commit ? `@${commit}` : undefined,
				ref ? `ref ${ref}` : undefined,
			].filter(Boolean);
			descriptor = `file: ${fileLabel}${extras.length > 0 ? ` ${extras.join(" ")}` : ""}`;
		}
	} else if (anchorType === "commit") {
		const sha = commit || shortSha(locator);
		if (sha) {
			descriptor = `commit: ${sha}${title ? ` ${title}` : ""}`;
		}
	} else if (anchorType === "pr") {
		const number = extractAnchorNumber(locator, "pr");
		if (number) {
			const extras = [title, commit ? `@${commit}` : undefined].filter(Boolean);
			descriptor = `PR #${number}${extras.length > 0 ? ` ${extras.join(" ")}` : ""}`;
		}
	} else if (anchorType === "issue") {
		const number = extractAnchorNumber(locator, "issue");
		if (number) {
			descriptor = `Issue #${number}${title ? ` ${title}` : ""}`;
		}
	}

	if (!descriptor) return null;
	if (mode === "derived" && source) {
		return `${mode} ${descriptor} via ${source}`;
	}
	return `${mode} ${descriptor}`;
}

function normalizeText(content: string): string {
	return content.toLowerCase().replace(/\s+/g, " ").trim();
}

function tokenize(content: string): string[] {
	return normalizeText(content)
		.replace(/[^a-z0-9\s]/g, " ")
		.split(/\s+/)
		.filter((token) => token.length > 2);
}

function tokenSimilarity(left: string, right: string): number {
	const leftSet = new Set(tokenize(left));
	const rightSet = new Set(tokenize(right));
	if (leftSet.size === 0 || rightSet.size === 0) return 0;
	let overlap = 0;
	for (const token of leftSet) {
		if (rightSet.has(token)) overlap += 1;
	}
	const union = leftSet.size + rightSet.size - overlap;
	return union === 0 ? 0 : overlap / union;
}

function contentFingerprint(content: string): string {
	return sha(normalizeText(content));
}

function topicSignature(content: string): string {
	const ignored = new Set([
		"this",
		"that",
		"with",
		"from",
		"have",
		"what",
		"when",
		"where",
		"which",
		"would",
		"could",
		"should",
	]);
	const counts = new Map<string, number>();
	for (const token of tokenize(content)) {
		if (ignored.has(token)) continue;
		counts.set(token, (counts.get(token) || 0) + 1);
	}
	return [...counts.entries()]
		.sort((a, b) => b[1] - a[1])
		.slice(0, 12)
		.map(([token]) => token)
		.sort()
		.join(" ");
}

function shouldRefreshForTopicShift(
	previousSignature: string,
	currentSignature: string,
): boolean {
	if (!previousSignature || !currentSignature) return false;
	return tokenSimilarity(previousSignature, currentSignature) < 0.35;
}

function shouldRetrieveForMessage(
	state: Pick<SessionState, "turn" | "lastInjectionTurn" | "topicSignature">,
	userMessage: string,
) {
	const signature = topicSignature(userMessage);
	const recallIntent = RECALL_INTENT_PATTERN.test(removeCodeParts(userMessage));
	const firstTurn = state.turn === 1 && MEM0_AUTO_RETRIEVE_FIRST_TURN;
	const periodicRefresh =
		state.lastInjectionTurn > 0 &&
		state.turn - state.lastInjectionTurn >= MEM0_REFRESH_EVERY_TURNS;
	const topicShift = shouldRefreshForTopicShift(
		state.topicSignature,
		signature,
	);

	return {
		signature,
		recallIntent,
		firstTurn,
		periodicRefresh,
		topicShift,
		shouldRetrieve: firstTurn || recallIntent || periodicRefresh || topicShift,
	};
}

function deriveHalfLifeDays(type: string): number {
	if (type === "decision" || type === "stable-fact") return 30;
	if (type === "procedure") return 7;
	if (type === "problem-fix") return 4;
	return 4;
}

const RECALL_INTENT_PATTERN =
	/\b(recall|what\s+did\s+we\s+decide|what\s+did\s+i\s+say|previously|earlier|past\s+decision|history|refresh\s+memory)\b/i;

async function sleep(ms: number): Promise<void> {
	await new Promise((resolve) => setTimeout(resolve, ms));
}

function createPartID(kind: string): string {
	return `prt-${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const CODE_BLOCK_PATTERN = /```[\s\S]*?```/g;
const INLINE_CODE_PATTERN = /`[^`]+`/g;
const MEMORY_KEYWORD_PATTERN =
	/\b(remember|save\s+this|don't\s+forget|dont\s+forget|store\s+this|note\s+this|memorize)\b/i;

function sha(input: string): string {
	return createHash("sha256").update(input).digest("hex").slice(0, 16);
}

function removeCodeParts(text: string): string {
	return text.replace(CODE_BLOCK_PATTERN, "").replace(INLINE_CODE_PATTERN, "");
}

function stripPrivateContent(content: string): string {
	return content.replace(/<private>[\s\S]*?<\/private>/gi, "[REDACTED]");
}

function isFullyPrivate(content: string): boolean {
	const stripped = stripPrivateContent(content).trim();
	return stripped === "" || stripped === "[REDACTED]";
}

function resolveUserID(): string {
	const explicit = process.env.MEM0_USER_ID || process.env.OPENCODE_USER_ID;
	if (explicit) return explicit;

	// WHY: A stable fallback keeps personal memory usable across sessions
	// even when explicit identity env vars are missing.
	const fallbackIdentity = `${process.env.USER || process.env.USERNAME || "anonymous"}@local`;
	return `oc-user-${sha(fallbackIdentity)}`;
}

function resolveProjectID(directory: string): string {
	// WHY: Hashing the absolute project path mirrors supermemory's deterministic
	// project scoping and prevents cross-project leakage.
	return `oc-project-${sha(directory)}`;
}

function resolveAgentID(agent?: string): string {
	return `oc-agent-${sha(agent || "default-agent")}`;
}

function scopeIdentifiers(
	scope: Scope,
	directory: string,
	agent?: string,
): { user_id: string; agent_id: string } {
	const userID = resolveUserID();
	const projectID = resolveProjectID(directory);
	const agentID = resolveAgentID(agent);

	if (scope === "user") {
		return { user_id: userID, agent_id: "oc-scope-user" };
	}
	if (scope === "project") {
		return { user_id: userID, agent_id: `oc-scope-project-${projectID}` };
	}
	if (scope === "agent") {
		return { user_id: userID, agent_id: `oc-scope-agent-${agentID}` };
	}

	return { user_id: userID, agent_id: `oc-scope-environment-${projectID}` };
}

function classifyHighSignal(
	content: string,
	explicitType?: MemoryType,
): { shouldSave: boolean; type: MemoryType; reason: string } {
	if (explicitType && explicitType !== "noise") {
		return {
			shouldSave: true,
			type: explicitType,
			reason: "Explicit high-signal type requested",
		};
	}

	const text = content.toLowerCase();

	const decision =
		/(decid(e|ed)|choose|selected|trade-?off|architecture|we will|going with)/i;
	const problemFix =
		/(bug|error|issue|fix|workaround|resolved|edge case|root cause)/i;
	const stableFact =
		/(preference|always|never|constraint|requires|must use|environment|stack|runtime|version)/i;
	const procedure =
		/(run this|steps|procedure|deploy|playbook|how to|checklist)/i;
	const noisy = /(thinking out loud|maybe|perhaps|temporary|for now|draft)/i;

	// WHY: The classifier intentionally biases toward precision over recall
	// to prevent low-value memory accumulation.
	if (decision.test(text))
		return {
			shouldSave: true,
			type: "decision",
			reason: "Detected durable decision signal",
		};
	if (problemFix.test(text))
		return {
			shouldSave: true,
			type: "problem-fix",
			reason: "Detected reusable problem-fix knowledge",
		};
	if (stableFact.test(text))
		return {
			shouldSave: true,
			type: "stable-fact",
			reason: "Detected stable preference/constraint",
		};
	if (procedure.test(text))
		return {
			shouldSave: true,
			type: "procedure",
			reason: "Detected reusable procedure",
		};
	if (noisy.test(text))
		return {
			shouldSave: false,
			type: "noise",
			reason: "Detected temporary/noisy content",
		};

	return {
		shouldSave: false,
		type: "noise",
		reason: "No high-signal pattern detected",
	};
}

function normalizeResults(payload: unknown): MemoryResult[] {
	const parsed = asRecord(payload);
	const list = parsed?.results || parsed?.memories || parsed?.data || [];
	if (!Array.isArray(list)) return [];

	return list
		.map((item) => {
			const row = asRecord(item);
			const metadata = asRecord(row?.metadata) || undefined;
			return {
				id: String(row?.id || row?.memory_id || ""),
				content: String(
					row?.memory ||
						row?.content ||
						row?.text ||
						row?.summary ||
						row?.chunk ||
						"",
				).trim(),
				score:
					typeof row?.score === "number"
						? row.score
						: typeof row?.similarity === "number"
							? row.similarity
							: undefined,
				anchor:
					row && hasOwnKey(row, "anchor")
						? normalizeAnchor(row.anchor)
						: normalizeAnchor(metadata?.anchor),
				metadata,
			};
		})
		.filter((item: { id: string; content: string }) => item.id && item.content);
}

let mem0BreakerFailures = 0;
let mem0BreakerOpenUntil = 0;
let mem0BreakerOpenCount = 0;

function timeoutForRequest(path: string, init: RequestInit): number {
	const method = String(init.method || "GET").toUpperCase();
	if (path === "/search" && method === "POST") {
		return MEM0_READ_TIMEOUT_MS;
	}
	if (method === "GET") {
		return MEM0_READ_TIMEOUT_MS;
	}
	return MEM0_WRITE_TIMEOUT_MS;
}

async function mem0Request(path: string, init: RequestInit): Promise<unknown> {
	if (!MEM0_SERVER_URL) {
		throw new Error("MEM0_SERVER_URL is not set");
	}

	if (Date.now() < mem0BreakerOpenUntil) {
		throw new Error(
			`mem0 circuit breaker open until ${new Date(mem0BreakerOpenUntil).toISOString()}`,
		);
	}

	let lastError: Error | null = null;
	let lastTimeoutMs = MEM0_READ_TIMEOUT_MS;

	for (let attempt = 1; attempt <= MEM0_API_MAX_ATTEMPTS; attempt += 1) {
		const timeoutMs = timeoutForRequest(path, init);
		lastTimeoutMs = timeoutMs;
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), timeoutMs);

		try {
			const response = await fetch(`${MEM0_SERVER_URL}${path}`, {
				...init,
				headers: {
					"Content-Type": "application/json",
					...(init.headers || {}),
				},
				signal: controller.signal,
			});

			const bodyText = await response.text();
			const parsed: unknown = bodyText ? JSON.parse(bodyText) : null;
			const parsedObj = asRecord(parsed);

			if (!response.ok) {
				throw new Error(
					String(
						parsedObj?.detail ||
							parsedObj?.error ||
							`mem0 request failed (${response.status})`,
					),
				);
			}

			mem0BreakerFailures = 0;
			return parsed;
		} catch (error) {
			lastError = error instanceof Error ? error : new Error(String(error));

			if (attempt >= MEM0_API_MAX_ATTEMPTS) {
				break;
			}

			await sleep(MEM0_API_RETRY_DELAY_MS * attempt);
		} finally {
			clearTimeout(timer);
		}
	}

	mem0BreakerFailures += 1;
	if (mem0BreakerFailures >= MEM0_BREAKER_THRESHOLD) {
		mem0BreakerOpenUntil = Date.now() + MEM0_BREAKER_COOLDOWN_MS;
		mem0BreakerFailures = 0;
		mem0BreakerOpenCount += 1;
	}

	throw new Error(
		`mem0 request failed after ${MEM0_API_MAX_ATTEMPTS} attempts (timeout=${lastTimeoutMs}ms): ${lastError?.message || "unknown error"}`,
	);
}

const mem0BackendClient = createMem0BackendClient({
	parseResults: normalizeResults,
	request: mem0Request,
});

function normalizeBackendRetrieveCandidates(
	payload: unknown,
	defaultScope: Scope,
): MemoryCandidate[] {
	const parsed = asRecord(payload);
	const results = Array.isArray(parsed?.results) ? parsed.results : [];
	const candidates: MemoryCandidate[] = [];

	for (const item of results) {
		const row = asRecord(item);
		if (!row) continue;

		const metadata = asRecord(row.metadata) || undefined;
		const anchor = hasOwnKey(row, "anchor")
			? normalizeAnchor(row.anchor)
			: normalizeAnchor(metadata?.anchor);
		const scope =
			asScope(row.scope) || asScope(metadata?.scope) || defaultScope;
		const type =
			typeof row.type === "string"
				? row.type
				: typeof metadata?.type === "string"
					? metadata.type
					: "unknown";
		const content = String(
			row.content || row.memory || row.text || row.summary || row.chunk || "",
		).trim();
		if (!content) continue;

		const score =
			typeof row.score === "number"
				? row.score
				: typeof row.similarity === "number"
					? row.similarity
					: undefined;
		const candidate: MemoryCandidate = {
			id: String(row.id || row.memory_id || ""),
			content,
			score,
			anchor,
			metadata,
			scope,
			type,
			fingerprint:
				typeof metadata?.fingerprint === "string"
					? metadata.fingerprint
					: contentFingerprint(content),
			inject: metadata?.inject !== false,
		};

		if (!candidate.id || !candidate.inject || candidate.type === "noise")
			continue;
		candidates.push(candidate);
	}

	return candidates;
}

async function searchScope(
	query: string,
	scope: Scope,
	directory: string,
	agent: string | undefined,
	limit = MAX_CONTEXT_ITEMS,
): Promise<MemoryResult[]> {
	const ids = scopeIdentifiers(scope, directory, agent);
	const payload = await mem0Request("/search", {
		method: "POST",
		body: JSON.stringify({
			query,
			...ids,
			filters: { scope },
		}),
	});

	return normalizeResults(payload).slice(0, limit);
}

function asScope(value: unknown): Scope | undefined {
	if (
		value === "user" ||
		value === "project" ||
		value === "agent" ||
		value === "environment"
	)
		return value;
	return undefined;
}

function updateLRU(list: string[], id: string): string[] {
	const filtered = list.filter((item) => item !== id);
	filtered.push(id);
	if (filtered.length <= MEM0_MAX_RECENT_IDS) return filtered;
	return filtered.slice(filtered.length - MEM0_MAX_RECENT_IDS);
}

const MEMORY_NUDGE = `[MEM0 MEMORY TRIGGER]
The user is asking you to remember something.
Use the \`mem0\` tool with \`mode: "add"\` and save only high-signal knowledge:
- decisions
- problems + fixes
- stable facts
- reusable procedures
Do NOT save raw conversations or temporary reasoning.`;

const COMPACTION_MEMORY_FALLBACK =
	"No verified Mem0 project memory was retrieved for this compaction.";

function buildCompactionMemoryBlock(
	projectMemories: ReadonlyArray<Pick<MemoryResult, "content">>,
): string | undefined {
	if (projectMemories.length === 0) return undefined;

	return [
		"## Mem0 High-Signal Project Memory",
		...projectMemories.map((memory) => `- ${memory.content}`),
		"Keep these durable facts in the continuation summary.",
	].join("\n");
}

function buildReplaceCompactionPrompt(
	projectMemories: ReadonlyArray<Pick<MemoryResult, "content">>,
): string {
	const memoryBlock =
		buildCompactionMemoryBlock(projectMemories) ||
		[
			"## Mem0 High-Signal Project Memory",
			`- ${COMPACTION_MEMORY_FALLBACK}`,
			"Keep these durable facts in the continuation summary.",
		].join("\n");

	return [
		"You are generating a continuation summary for an OpenCode session.",
		"Preserve only durable, actionable context.",
		"",
		"Return markdown with these exact section headings in this exact order:",
		"## Final Goal",
		"## Completed Work",
		"## Remaining Tasks",
		"## Critical Constraints",
		"## Mem0 High-Signal Project Memory",
		"",
		"For the final section, include the Mem0 heading exactly as written and reproduce the provided bullet list verbatim.",
		"Do not omit the Mem0 section, even when only the fallback memory is available.",
		"",
		memoryBlock,
	].join("\n");
}

export const Mem0FunctionalPlugin: Plugin = async (ctx) => {
	const sessionState = new Map<string, SessionState>();
	const sessionDirectory = new Map<string, string>();
	const coldCompactionPending = new Set<string>();
	const metrics: PluginMetrics = {
		retrievalAttempts: 0,
		retrievalHits: 0,
		dedupeDrops: 0,
		budgetDrops: 0,
		breakerOpens: 0,
	};

	function getSessionState(sessionID: string): SessionState {
		const existing = sessionState.get(sessionID);
		if (existing) return existing;
		const created: SessionState = {
			turn: 0,
			lastInjectionTurn: 0,
			topicSignature: "",
			injectedMemoryIdsLRU: [],
			workingSet: [],
			lastGoodContextSnippet: "",
		};
		sessionState.set(sessionID, created);
		return created;
	}

	function setSessionDirectory(sessionID: string, directory?: string): void {
		if (!directory) return;
		sessionDirectory.set(sessionID, directory);
	}

	function getSessionDirectory(sessionID: string): string {
		return sessionDirectory.get(sessionID) || ctx.directory;
	}

	async function appendDebugEvent(
		event: string,
		payload: JsonRecord,
	): Promise<void> {
		if (!shouldWriteDebugFile()) return;

		try {
			await mkdir(dirname(MEM0_DEBUG_LOG_PATH), { recursive: true });
			await appendFile(
				MEM0_DEBUG_LOG_PATH,
				`${JSON.stringify({
					ts: new Date().toISOString(),
					event,
					...payload,
				})}\n`,
				"utf8",
			);
		} catch (error) {
			console.warn(
				"[mem0-functional-plugin] failed to write debug event",
				error instanceof Error ? error.message : String(error),
			);
		}
	}

	function shouldLogLifecycleDebug(): boolean {
		return MEM0_LOG_INJECTION || MEM0_DEBUG_PROMPTS;
	}

	async function appendLifecycleDebugEvent(
		event: string,
		payload: JsonRecord,
	): Promise<void> {
		if (!shouldLogLifecycleDebug()) return;

		await appendDebugEvent(event, {
			directory: ctx.directory,
			service: "mem0-functional-plugin",
			...payload,
		});
	}

	async function logInjection(
		message: string,
		extra?: JsonRecord,
		directory = ctx.directory,
	): Promise<void> {
		if (!MEM0_LOG_INJECTION) return;

		await appendDebugEvent("mem0.injection", {
			directory,
			service: "mem0-functional-plugin",
			level: "info",
			message,
			extra: {
				...extra,
				metrics,
				breakerOpenCount: mem0BreakerOpenCount,
			},
		});

		try {
			await ctx.client.app.log({
				body: {
					service: "mem0-functional-plugin",
					level: "info",
					message,
					extra: {
						...extra,
						metrics,
						breakerOpenCount: mem0BreakerOpenCount,
					},
				},
				query: { directory },
			});
		} catch (error) {
			console.warn(
				"[mem0-functional-plugin] failed to write app log",
				error instanceof Error ? error.message : String(error),
			);
			return;
		}
	}

	async function detectSupersedes(
		content: string,
		type: string,
		scope: Scope,
		directory: string,
	): Promise<string[]> {
		const related = await searchScope(
			content.slice(0, 220),
			scope,
			directory,
			undefined,
			12,
		);
		const supersedes: string[] = [];
		for (const row of related) {
			const existingType =
				typeof row.metadata?.type === "string" ? row.metadata.type : undefined;
			if (existingType && existingType !== type) continue;
			if (tokenSimilarity(content, row.content) >= MEM0_SUPERSEDES_THRESHOLD) {
				supersedes.push(row.id);
			}
		}
		return supersedes;
	}

	async function retrieveBackendCandidates(
		query: string,
		directory: string,
		agent?: string,
	): Promise<MemoryCandidate[]> {
		metrics.retrievalAttempts += 1;
		const identifiers = scopeIdentifiers("project", directory, agent);
		const response = await mem0BackendClient.search({
			query,
			scope: "project",
			scopes: ["user", "project", "agent", "environment"],
			identifiers,
			limit: MAX_CONTEXT_ITEMS,
		});

		const candidates = normalizeBackendRetrieveCandidates(
			response.payload,
			"project",
		);
		if (candidates.length > 0) {
			metrics.retrievalHits += 1;
		}

		return candidates;
	}

	function buildContextText(candidates: MemoryCandidate[]): string {
		const grouped: Record<Scope, MemoryCandidate[]> = {
			user: [],
			project: [],
			agent: [],
			environment: [],
		};

		for (const candidate of candidates) {
			grouped[candidate.scope].push(candidate);
		}

		const sections = [
			{ scope: "user" as const, title: "User Context" },
			{ scope: "project" as const, title: "Project Context" },
			{ scope: "agent" as const, title: "Agent Context" },
			{ scope: "environment" as const, title: "Environment Context" },
		]
			.map(({ scope, title }) => {
				const rows = grouped[scope];
				if (rows.length === 0) return "";
				const lines = rows.map((row) => {
					const anchorLabel = formatAnchorLabel(row.anchor);
					return `- [${row.type}] ${row.content}${anchorLabel ? ` [${anchorLabel}]` : ""}`;
				});
				return `${title}:\n${lines.join("\n")}`;
			})
			.filter(Boolean);

		return `[MEM0 CONTEXT]\n${sections.join("\n\n")}`;
	}

	function selectForInjection(
		ranked: MemoryCandidate[],
		session: SessionState,
	): MemoryCandidate[] {
		const selected: MemoryCandidate[] = [];
		const seenFingerprints = new Set<string>();
		let usedChars = "[MEM0 CONTEXT]\n".length;

		for (const candidate of ranked) {
			if (selected.length >= MAX_CONTEXT_ITEMS) {
				metrics.budgetDrops += 1;
				break;
			}
			if (session.injectedMemoryIdsLRU.includes(candidate.id)) {
				metrics.dedupeDrops += 1;
				continue;
			}
			if (seenFingerprints.has(candidate.fingerprint)) {
				metrics.dedupeDrops += 1;
				continue;
			}
			const nearDuplicate = selected.some(
				(row) =>
					tokenSimilarity(row.content, candidate.content) >=
					MEM0_SIMILARITY_DEDUPE_THRESHOLD,
			);
			if (nearDuplicate) {
				metrics.dedupeDrops += 1;
				continue;
			}
			const nextCost = candidate.content.length + 40;
			if (usedChars + nextCost > MEM0_MAX_INJECT_CHARS) {
				metrics.budgetDrops += 1;
				continue;
			}

			usedChars += nextCost;
			seenFingerprints.add(candidate.fingerprint);
			selected.push(candidate);
		}

		return selected;
	}

	async function saveColdCompactionMemory(
		sessionID: string,
		messageID: string,
		directoryHint?: string,
	): Promise<void> {
		if (!MEM0_SAVE_COLD_COMPACTION) return;

		try {
			const messageResult = await ctx.client.session.message({
				path: { id: sessionID, messageID },
				query: { directory: directoryHint || ctx.directory },
			});

			const data = asRecord(messageResult?.data);
			const summaryText = textFromParts(data?.parts);
			if (!summaryText) return;

			const boundedSummary = clampText(
				summaryText,
				Number.isFinite(MEM0_COLD_MAX_CHARS) ? MEM0_COLD_MAX_CHARS : 6000,
			);
			const ids = scopeIdentifiers(
				"project",
				directoryHint || ctx.directory,
				undefined,
			);

			await mem0Request("/memories", {
				method: "POST",
				body: JSON.stringify({
					messages: [
						{
							role: "assistant",
							content: `[Compaction Summary]\n${boundedSummary}`,
						},
					],
					...ids,
					metadata: {
						source: "opencode-plugin",
						scope: "project",
						type: "compaction-archive",
						tier: "long-term",
						cold_context: true,
						inject: false,
						session_id: sessionID,
						created_at: new Date().toISOString(),
						last_used_at: new Date().toISOString(),
						access_count: 0,
						fingerprint: contentFingerprint(boundedSummary),
						decay_half_life_days: deriveHalfLifeDays("compaction-archive"),
					},
				}),
			});

			await logInjection(
				"cold compaction archived",
				{
					sessionID,
					summaryChars: boundedSummary.length,
					coldContext: true,
				},
				directoryHint || ctx.directory,
			);
		} catch {
			return;
		}
	}

	await appendLifecycleDebugEvent("mem0.lifecycle", {
		stage: "plugin.init",
		serverURL: MEM0_SERVER_URL,
		autoRetrieveFirstTurn: MEM0_AUTO_RETRIEVE_FIRST_TURN,
		refreshEveryTurns: MEM0_REFRESH_EVERY_TURNS,
		debugPrompts: MEM0_DEBUG_PROMPTS,
		logInjection: MEM0_LOG_INJECTION,
	});

	return {
		"chat.message": async (input, output) => {
			if (!MEM0_SERVER_URL) return;

			const activeDirectory = getSessionDirectory(input.sessionID);

			const textParts = output.parts.filter(
				(part): part is Part & { type: "text"; text: string } =>
					part.type === "text" && typeof part.text === "string",
			);
			if (textParts.length === 0) return;

			const userMessage = textParts
				.map((part) => part.text)
				.join("\n")
				.trim();
			if (!userMessage) return;

			const state = getSessionState(input.sessionID);
			state.turn += 1;

			if (MEMORY_KEYWORD_PATTERN.test(removeCodeParts(userMessage))) {
				const nudgePart: Part = {
					id: createPartID("nudge"),
					sessionID: input.sessionID,
					messageID: output.message.id,
					type: "text",
					text: MEMORY_NUDGE,
					synthetic: true,
				};
				output.parts.push(nudgePart);
				await logInjection(
					"memory nudge injected",
					{
						sessionID: input.sessionID,
						messageID: output.message.id,
						partID: nudgePart.id,
						text: MEM0_LOG_INJECTION_CONTENT
							? preview(MEMORY_NUDGE)
							: undefined,
					},
					activeDirectory,
				);
			}

			const retrievalDecision = shouldRetrieveForMessage(state, userMessage);
			const {
				signature,
				recallIntent,
				firstTurn,
				periodicRefresh,
				topicShift,
				shouldRetrieve,
			} = retrievalDecision;

			state.topicSignature = signature;
			if (!shouldRetrieve) {
				await appendLifecycleDebugEvent("mem0.lifecycle", {
					stage: "chat.message.skip",
					sessionID: input.sessionID,
					messageID: output.message.id,
					turn: state.turn,
					reason: { firstTurn, recallIntent, periodicRefresh, topicShift },
				});
				return;
			}

			try {
				const ranked = await retrieveBackendCandidates(
					userMessage,
					activeDirectory,
					input.agent,
				);
				const selected = selectForInjection(ranked, state);
				if (selected.length === 0) {
					await appendLifecycleDebugEvent("mem0.lifecycle", {
						stage: "retrieval.empty",
						sessionID: input.sessionID,
						messageID: output.message.id,
						turn: state.turn,
						rankedCount: ranked.length,
						selectedCount: 0,
						reason: { firstTurn, recallIntent, periodicRefresh, topicShift },
					});
					return;
				}
				const contextText = buildContextText(selected);

				const contextPart: Part = {
					id: createPartID("context"),
					sessionID: input.sessionID,
					messageID: output.message.id,
					type: "text",
					text: contextText,
					synthetic: true,
				};
				output.parts.unshift(contextPart);

				state.lastInjectionTurn = state.turn;
				state.workingSet = selected;
				state.lastGoodContextSnippet = contextText;
				for (const memory of selected) {
					state.injectedMemoryIdsLRU = updateLRU(
						state.injectedMemoryIdsLRU,
						memory.id,
					);
				}

				await logInjection(
					"memory context injected",
					{
						sessionID: input.sessionID,
						messageID: output.message.id,
						partID: contextPart.id,
						reason: { firstTurn, recallIntent, periodicRefresh, topicShift },
						selected: selected.length,
						text: MEM0_LOG_INJECTION_CONTENT
							? preview(contextPart.text)
							: undefined,
					},
					activeDirectory,
				);
			} catch (error) {
				await appendLifecycleDebugEvent("mem0.lifecycle", {
					stage: "retrieval.error",
					sessionID: input.sessionID,
					messageID: output.message.id,
					turn: state.turn,
					fallbackInjected: Boolean(state.lastGoodContextSnippet),
					reason: { firstTurn, recallIntent, periodicRefresh, topicShift },
					error: error instanceof Error ? error.message : String(error),
				});
				if (state.lastGoodContextSnippet) {
					const fallbackPart: Part = {
						id: createPartID("fallback-context"),
						sessionID: input.sessionID,
						messageID: output.message.id,
						type: "text",
						text: `[MEM0 CONTEXT FALLBACK]\n${state.lastGoodContextSnippet}`,
						synthetic: true,
					};
					output.parts.unshift(fallbackPart);
					await logInjection(
						"memory fallback context injected",
						{
							sessionID: input.sessionID,
							messageID: output.message.id,
							partID: fallbackPart.id,
							error: error instanceof Error ? error.message : String(error),
						},
						activeDirectory,
					);
				}
				// WHY: Memory retrieval must degrade gracefully; chat flow should never fail
				// just because the memory server is unavailable.
				return;
			}
		},

		tool: {
			mem0: tool({
				description: "Persist and query high-signal memory via mem0 REST.",
				args: {
					mode: tool.schema
						.enum(["add", "search", "list", "forget", "help"])
						.optional(),
					content: tool.schema.string().optional(),
					query: tool.schema.string().optional(),
					scope: tool.schema
						.enum(["user", "project", "agent", "environment"])
						.optional(),
					type: tool.schema
						.enum([
							"decision",
							"problem-fix",
							"stable-fact",
							"procedure",
							"noise",
						])
						.optional(),
					anchor: mem0AnchorSchema.optional(),
					anchorContext: mem0AnchorContextSchema.optional(),
					memoryId: tool.schema.string().optional(),
					limit: tool.schema.number().optional(),
				},
				async execute(
					args: {
						mode?: "add" | "search" | "list" | "forget" | "help";
						content?: string;
						query?: string;
						scope?: Scope;
						type?: MemoryType;
						anchor?: Mem0Anchor;
						anchorContext?: Mem0AnchorContext;
						memoryId?: string;
						limit?: number;
					},
					toolContext,
				) {
					if (!MEM0_SERVER_URL) {
						return JSON.stringify({
							success: false,
							error:
								"MEM0_SERVER_URL is not set. Example: export MEM0_SERVER_URL=http://localhost:8000",
						});
					}

					const mode = args.mode || "help";
					const scope = args.scope || "project";
					const ids = scopeIdentifiers(scope, toolContext.directory, undefined);

					try {
						await appendLifecycleDebugEvent("mem0.tool", {
							stage: "start",
							sessionID: toolContext.sessionID,
							directory: toolContext.directory,
							mode,
							scope,
						});

						if (mode === "help") {
							const response = {
								success: true,
								modes: ["add", "search", "list", "forget", "help"],
								scopes: ["user", "project", "agent", "environment"],
								note: "Only high-signal memories should be stored. Optional add-mode anchoring can be passed via anchor or anchorContext; safe git context is auto-enriched when available.",
							};
							await appendLifecycleDebugEvent("mem0.tool", {
								stage: "success",
								sessionID: toolContext.sessionID,
								directory: toolContext.directory,
								mode,
								scope,
							});
							return JSON.stringify(response);
						}

						if (mode === "add") {
							setSessionDirectory(toolContext.sessionID, toolContext.directory);
							if (!args.content) {
								return JSON.stringify({
									success: false,
									error: "content is required for add mode",
								});
							}

							if (isFullyPrivate(args.content)) {
								return JSON.stringify({
									success: false,
									error: "Cannot store fully private content",
								});
							}

							const sanitized = stripPrivateContent(args.content);
							const quality = classifyHighSignal(sanitized, args.type);
							if (!quality.shouldSave) {
								return JSON.stringify({
									success: true,
									skipped: true,
									reason: quality.reason,
								});
							}

							const supersedes = await detectSupersedes(
								sanitized,
								quality.type,
								scope,
								toolContext.directory,
							);
							const anchor = normalizeAnchorWriteInput(args.anchor);
							const mergedAnchorContext = anchor
								? undefined
								: mergeAnchorContexts(
										inferAutomaticAnchorContext(toolContext.directory),
										args.anchorContext,
									);
							const anchorContext =
								normalizeAnchorContextWriteInput(mergedAnchorContext);
							const metadata: JsonRecord = {
								source: "opencode-plugin",
								scope,
								type: quality.type,
								tier: "long-term",
								project_id: resolveProjectID(toolContext.directory),
								created_at: new Date().toISOString(),
								last_used_at: new Date().toISOString(),
								access_count: 0,
								fingerprint: contentFingerprint(sanitized),
								decay_half_life_days: deriveHalfLifeDays(quality.type),
								inject: true,
								supersedes,
							};

							if (anchor) {
								metadata.anchor = anchor;
							}
							if (anchorContext) {
								metadata.anchor_context = anchorContext;
							}

							const payload = await mem0Request("/memories", {
								method: "POST",
								body: JSON.stringify({
									messages: [{ role: "user", content: sanitized }],
									...ids,
									metadata,
								}),
							});

							const payloadData = asRecord(payload);
							const createdMemory = asRecord(payloadData?.memory);
							const response = {
								success: true,
								scope,
								type: quality.type,
								reason: quality.reason,
								id: payloadData?.id,
								anchor:
									normalizeAnchor(createdMemory?.anchor) ||
									normalizeAnchor(payloadData?.anchor),
								identifiers: ids,
								project_id: resolveProjectID(toolContext.directory),
								supersedes,
							};
							await appendLifecycleDebugEvent("mem0.tool", {
								stage: "success",
								sessionID: toolContext.sessionID,
								directory: toolContext.directory,
								mode,
								scope,
							});
							return JSON.stringify(response);
						}

						if (mode === "search") {
							setSessionDirectory(toolContext.sessionID, toolContext.directory);
							if (!args.query) {
								return JSON.stringify({
									success: false,
									error: "query is required for search mode",
								});
							}

							const searchResponse = await mem0BackendClient.search({
								query: args.query,
								identifiers: ids,
								limit: args.limit,
								scope,
							});
							const toolResponse = {
								success: true,
								scope,
								endpoint: searchResponse.endpoint,
								backend_capabilities: searchResponse.backendCapabilities,
								degraded: searchResponse.degraded,
								degradation_reasons: searchResponse.degradationReasons,
								request_id: searchResponse.requestId,
								count: searchResponse.results.length,
								results: searchResponse.results,
							};
							await appendLifecycleDebugEvent("mem0.tool", {
								stage: "success",
								sessionID: toolContext.sessionID,
								directory: toolContext.directory,
								mode,
								scope,
								resultCount: toolResponse.count,
							});
							return JSON.stringify(toolResponse);
						}

						if (mode === "list") {
							setSessionDirectory(toolContext.sessionID, toolContext.directory);
							const query = new URLSearchParams();
							query.set("user_id", ids.user_id);
							query.set("agent_id", ids.agent_id);
							const payload = await mem0Request(
								`/memories?${query.toString()}`,
								{ method: "GET" },
							);

							const results = normalizeResults(payload).slice(
								0,
								args.limit || 20,
							);
							await appendLifecycleDebugEvent("mem0.tool", {
								stage: "success",
								sessionID: toolContext.sessionID,
								directory: toolContext.directory,
								mode,
								scope,
								resultCount: results.length,
							});
							return JSON.stringify({
								success: true,
								scope,
								count: results.length,
								results,
							});
						}

						if (mode === "forget") {
							setSessionDirectory(toolContext.sessionID, toolContext.directory);
							if (!args.memoryId) {
								return JSON.stringify({
									success: false,
									error: "memoryId is required for forget mode",
								});
							}

							await mem0Request(
								`/memories/${encodeURIComponent(args.memoryId)}`,
								{ method: "DELETE" },
							);
							await appendLifecycleDebugEvent("mem0.tool", {
								stage: "success",
								sessionID: toolContext.sessionID,
								directory: toolContext.directory,
								mode,
								scope,
							});
							return JSON.stringify({ success: true, deleted: args.memoryId });
						}

						return JSON.stringify({
							success: false,
							error: `Unknown mode: ${mode}`,
						});
					} catch (error) {
						await appendLifecycleDebugEvent("mem0.tool", {
							stage: "error",
							sessionID: toolContext.sessionID,
							directory: toolContext.directory,
							mode,
							scope,
							error: error instanceof Error ? error.message : String(error),
						});
						return JSON.stringify({
							success: false,
							error: error instanceof Error ? error.message : String(error),
						});
					}
				},
			}),
		},

		"chat.params": async (input, output) => {
			if (!MEM0_DEBUG_PROMPTS) return;

			const providerInfo = asRecord(input.provider.info as unknown);
			const modelInfo = asRecord(input.model as unknown);
			const messageInfo = asRecord(input.message as unknown);

			await appendDebugEvent("provider.params", {
				sessionID: input.sessionID,
				agent: input.agent,
				providerSource: input.provider.source,
				providerID:
					typeof providerInfo?.id === "string" ? providerInfo.id : undefined,
				modelID: typeof modelInfo?.id === "string" ? modelInfo.id : undefined,
				messageID:
					typeof messageInfo?.id === "string" ? messageInfo.id : undefined,
				temperature: output.temperature,
				topP: output.topP,
				topK: output.topK,
				optionKeys: Object.keys(output.options || {}),
			});
		},

		"experimental.chat.messages.transform": async (_input, output) => {
			if (!MEM0_DEBUG_PROMPTS) return;

			const messages = summarizeMessages(output.messages);
			const lastMessage = messages[messages.length - 1];
			const markerMatches = messages.filter(
				(message) => message.hasMem0Context === true,
			).length;

			await appendDebugEvent("provider.messages", {
				sessionID:
					typeof lastMessage?.sessionID === "string"
						? lastMessage.sessionID
						: undefined,
				messageCount: messages.length,
				markerMatches,
				hasMem0Context: markerMatches > 0,
				messages,
			});
		},

		"experimental.chat.system.transform": async (input, output) => {
			if (!MEM0_DEBUG_PROMPTS) return;

			const systemText = output.system.join("\n\n");
			const modelInfo = asRecord(input.model as unknown);

			await appendDebugEvent("provider.system", {
				sessionID: input.sessionID,
				modelID: typeof modelInfo?.id === "string" ? modelInfo.id : undefined,
				systemCount: output.system.length,
				hasMem0Context: systemText.includes("[MEM0 CONTEXT]"),
				system: MEM0_DEBUG_PROMPT_CONTENT
					? sanitizeDebugText(systemText)
					: undefined,
			});
		},

		/**
		 * Retrieves at most eight memories for the active directory's project scope via
		 * `POST /retrieve`. Unlike supersession detection, it never calls `POST /search`;
		 * unlike chat recall's all-scope request, it requests only the project scope. If
		 * the server URL is unavailable, the handler is a no-op. Otherwise, append mode
		 * leaves prompt and context unchanged on empty results or retrieval failure;
		 * replace mode writes either a memory-aware prompt or its existing no-memory
		 * fallback. With cold storage enabled, the session is marked pending before
		 * retrieval, so empty results and failures remain eligible for archival.
		 */
		"experimental.session.compacting": async (_input, output) => {
			if (!MEM0_SERVER_URL) return;

			const activeDirectory = getSessionDirectory(_input.sessionID);

			if (MEM0_SAVE_COLD_COMPACTION) {
				coldCompactionPending.add(_input.sessionID);
			}

			try {
				const projectMemories = (
					await mem0BackendClient.search({
						query: "recent decisions fixes constraints procedures",
						scope: "project",
						identifiers: scopeIdentifiers("project", activeDirectory),
						limit: 8,
					})
				).results;

				if (MEM0_COMPACTION_MODE === "replace") {
					// WHY: Some teams want deterministic compaction output and prefer
					// owning the entire summary template. This mode is opt-in because
					// replacing defaults can remove useful built-in continuity guidance.
					output.prompt = buildReplaceCompactionPrompt(projectMemories);
					await appendDebugEvent("mem0.compaction", {
						sessionID: _input.sessionID,
						directory: activeDirectory,
						mode: MEM0_COMPACTION_MODE,
						branch: "replace",
						memoryCount: projectMemories.length,
						prompt: MEM0_DEBUG_PROMPT_CONTENT
							? sanitizeDebugText(output.prompt)
							: undefined,
					});
					return;
				}

				const memoryBlock = buildCompactionMemoryBlock(projectMemories);
				if (!memoryBlock) {
					await appendDebugEvent("mem0.compaction", {
						sessionID: _input.sessionID,
						directory: activeDirectory,
						mode: MEM0_COMPACTION_MODE,
						branch: "append",
						memoryCount: projectMemories.length,
						memoryBlockIncluded: false,
					});
					return;
				}

				output.context.push(memoryBlock);
				await appendDebugEvent("mem0.compaction", {
					sessionID: _input.sessionID,
					directory: activeDirectory,
					mode: MEM0_COMPACTION_MODE,
					branch: "append",
					memoryCount: projectMemories.length,
					memoryBlockIncluded: true,
					memoryBlock: MEM0_DEBUG_PROMPT_CONTENT
						? sanitizeDebugText(memoryBlock)
						: undefined,
				});
			} catch {
				if (MEM0_COMPACTION_MODE === "replace") {
					output.prompt = buildReplaceCompactionPrompt([]);
					await appendDebugEvent("mem0.compaction", {
						sessionID: _input.sessionID,
						directory: activeDirectory,
						mode: MEM0_COMPACTION_MODE,
						branch: "replace",
						memoryCount: 0,
						fallbackUsed: true,
						prompt: MEM0_DEBUG_PROMPT_CONTENT
							? sanitizeDebugText(output.prompt)
							: undefined,
					});
					return;
				}

				await appendDebugEvent("mem0.compaction", {
					sessionID: _input.sessionID,
					directory: activeDirectory,
					mode: MEM0_COMPACTION_MODE,
					branch: "append",
					memoryCount: 0,
					fallbackUsed: true,
				});
				return;
			}
		},

		event: async ({ event }) => {
			if (!MEM0_SERVER_URL) return;

			if (event.type === "message.updated" && MEM0_SAVE_COLD_COMPACTION) {
				// WHY: OpenCode emits `message.updated` for many message states, but only
				// summary assistant messages (`summary=true` and finished) represent the
				// compaction output we want to archive as cold context.
				const props = asRecord(event.properties);
				const info = asRecord(props?.info);
				const sessionID =
					typeof info?.sessionID === "string" ? info.sessionID : undefined;
				const messageID = typeof info?.id === "string" ? info.id : undefined;
				const role = info?.role;
				const summary = info?.summary;
				const finish = info?.finish;
				const pathInfo = asRecord(info?.path);
				const directoryHint =
					typeof pathInfo?.cwd === "string" ? pathInfo.cwd : undefined;

				if (sessionID) {
					setSessionDirectory(sessionID, directoryHint);
				}

				if (
					sessionID &&
					messageID &&
					coldCompactionPending.has(sessionID) &&
					role === "assistant" &&
					summary === true &&
					Boolean(finish)
				) {
					// WHY: We remove the session from the pending set before the write to
					// enforce at-most-once archival per compaction cycle.
					coldCompactionPending.delete(sessionID);
					await saveColdCompactionMemory(sessionID, messageID, directoryHint);
				}
			}

			if (event.type === "session.deleted") {
				// WHY: Prevent unbounded growth in session tracking.
				const props = asRecord(event.properties);
				const sessionInfo = asRecord(props?.info);
				const sessionID =
					typeof sessionInfo?.id === "string" ? sessionInfo.id : undefined;
				if (sessionID) {
					sessionState.delete(sessionID);
					sessionDirectory.delete(sessionID);
					coldCompactionPending.delete(sessionID);
				}
			}
		},
	};
};

export const __test__ = Object.assign(Mem0FunctionalPlugin, {
	inferAnchorContextType,
	mergeAnchorContexts,
	inferAutomaticAnchorContext,
	normalizeGitRepoIdentifier,
	buildCompactionMemoryBlock,
	buildReplaceCompactionPrompt,
	shouldRetrieveForMessage,
});

export default Mem0FunctionalPlugin;
