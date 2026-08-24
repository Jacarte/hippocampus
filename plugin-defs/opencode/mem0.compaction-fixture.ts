import type { PluginInput } from "@opencode-ai/plugin";

const [backendMode, compactionMode, outcome] = process.argv.slice(2);
const directory = "/tmp/mem0-compaction-project";

if (
	(backendMode !== "legacy" && backendMode !== "backend") ||
	(compactionMode !== "append" && compactionMode !== "replace") ||
	(outcome !== "success" && outcome !== "failure")
) {
	throw new Error("Expected backend mode, compaction mode, and outcome arguments");
}

process.env.MEM0_BACKEND_MODE = backendMode;
process.env.MEM0_COMPACTION_MODE = compactionMode;
process.env.MEM0_SERVER_URL = "http://mem0.test";
process.env.MEM0_USER_ID = "fixture-user";
process.env.MEM0_SAVE_COLD_COMPACTION = "0";

interface RecordedRequest {
	/** Absolute URL received by the fetch replacement. */
	url: string;
	/** HTTP method received by the fetch replacement. */
	method: string;
	/** Parsed JSON body, or undefined when the request has no body. */
	body: unknown;
}

const requests: RecordedRequest[] = [];

/** Records requests and returns deterministic success or retryable-failure responses. */
globalThis.fetch = Object.assign(
	async (
		input: string | URL | Request,
		init?: RequestInit,
	): Promise<Response> => {
		const request = input instanceof Request ? input : undefined;
		const bodyText = request
			? await request.clone().text()
			: String(init?.body ?? "");
		requests.push({
			url: request?.url ?? String(input),
			method: request?.method ?? String(init?.method ?? "GET"),
			body: bodyText ? JSON.parse(bodyText) : undefined,
		});

		if (outcome === "failure") {
			return Response.json(
				{ detail: "fixture retrieval failure" },
				{ status: 503 },
			);
		}

		return Response.json({
			results: [
				{
					id: "memory-1",
					memory: "Use retrieve for backend compaction.",
					score: 0.95,
				},
			],
		});
	},
	{ preconnect: globalThis.fetch.preconnect },
);

const context = {
	client: {} as PluginInput["client"],
	project: {
		id: "fixture-project",
		worktree: directory,
		time: { created: 0 },
	},
	directory,
	worktree: directory,
	experimental_workspace: { register: () => undefined },
	serverUrl: new URL("http://opencode.test"),
	$: {} as PluginInput["$"],
} satisfies PluginInput;

const { Mem0FunctionalPlugin } = await import("./mem0.ts");
const hooks = await Mem0FunctionalPlugin(context);
const compact = hooks["experimental.session.compacting"];

if (!compact) {
	throw new Error("Mem0FunctionalPlugin did not register the compaction hook");
}

const output: {
	/** Existing context that append mode may extend. */
	context: string[];
	/** Existing prompt that replace mode may overwrite. */
	prompt?: string;
} = {
	context: ["existing context"],
	prompt: "existing prompt",
};

await compact({ sessionID: "fixture-session" }, output);

process.stdout.write(JSON.stringify({ requests, output }));
