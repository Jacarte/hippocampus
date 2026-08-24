import { createHash } from "node:crypto";
import { join } from "node:path";
import { describe, expect, test } from "bun:test";

type BackendMode = "legacy" | "backend";
type CompactionMode = "append" | "replace";
type Outcome = "success" | "failure";

interface RecordedRequest {
	url: string;
	method: string;
	body: Record<string, unknown>;
}

interface FixtureResult {
	requests: RecordedRequest[];
	output: {
		context: string[];
		prompt?: string;
	};
}

/** Runs one isolated compaction scenario so module-level environment is fresh. */
function runCompactionScenario(
	backendMode: BackendMode,
	compactionMode: CompactionMode,
	outcome: Outcome,
): FixtureResult {
	const processResult = Bun.spawnSync([
		process.execPath,
		join(import.meta.dir, "mem0.compaction-fixture.ts"),
		backendMode,
		compactionMode,
		outcome,
	]);

	expect(new TextDecoder().decode(processResult.stderr)).toBe("");
	expect(processResult.exitCode).toBe(0);

	return JSON.parse(
		new TextDecoder().decode(processResult.stdout),
	) as FixtureResult;
}

/** Returns the deterministic project identifiers expected from the fixture directory. */
function expectedIdentifiers(): { user_id: string; agent_id: string } {
	const digest = createHash("sha256")
		.update("/tmp/mem0-compaction-project")
		.digest("hex")
		.slice(0, 16);
	return {
		user_id: "fixture-user",
		agent_id: `oc-scope-project-oc-project-${digest}`,
	};
}

describe("Mem0FunctionalPlugin", () => {
	test(
		"actual compaction hook request and fallback matrix",
		() => {
			const query = "recent decisions fixes constraints procedures";

			for (const backendMode of ["legacy", "backend"] as const) {
				for (const compactionMode of ["append", "replace"] as const) {
					for (const outcome of ["success", "failure"] as const) {
						const result = runCompactionScenario(
							backendMode,
							compactionMode,
							outcome,
						);
						const expectedPath = backendMode === "backend" ? "/retrieve" : "/search";
						const expectedBody =
							backendMode === "backend"
								? {
									query,
									scopes: ["project"],
									...expectedIdentifiers(),
									limit: 8,
									filters: { include_cold_context: false },
								}
								: {
									query,
									...expectedIdentifiers(),
									filters: { scope: "project" },
								};

						expect(result.requests).toHaveLength(outcome === "success" ? 1 : 3);
						for (const request of result.requests) {
							expect(request.method).toBe("POST");
							expect(request.url).toBe(`http://mem0.test${expectedPath}`);
							expect(request.body).toEqual(expectedBody);
						}

						if (compactionMode === "append") {
							expect(result.output.prompt).toBe("existing prompt");
							expect(result.output.context).toEqual(
								outcome === "success"
									? [
										"existing context",
										"## Mem0 High-Signal Project Memory\n- Use retrieve for backend compaction.\nKeep these durable facts in the continuation summary.",
									]
									: ["existing context"],
							);
						} else {
							expect(result.output.context).toEqual(["existing context"]);
							expect(result.output.prompt).toContain(
								outcome === "success"
									? "- Use retrieve for backend compaction."
									: "- No verified Mem0 project memory was retrieved for this compaction.",
							);
						}
					}
				}
			}
		},
		20_000,
	);
});
