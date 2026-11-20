// Recall stress workflow: 18 cheap read-only scouts in 3 waves of 6, each opening with a different
// kind of prompt so hippocampus auto-recall (shaped query -> /retrieve) is exercised from subagents.
// Run it with: subagent({ workflow: "monitoring/stress/recall-stress.js", async: true, model: "litellm/bedrock-claude-haiku-5-5",
//   options: { usageBudget: { costUsd: { soft: 1, hard: 2 } } } })
// Then read the results with the Loki queries in monitoring/stress/README.md.
const BOILER = "Task: Before substantive work call jev_route action switch_effort for your own role. ";
const TAIL = " Read-only: do not edit anything. Use at most one shell command. Reply with one short line only.";
const CTX = " Context that makes real briefs long: dashboards are provisioned from JSON, reloaded every 10 seconds, every panel has a description with Read, Look for and Example, datasources are Prometheus, Loki uid loki and Postgres uid mem0-pg, and decay only applies on retrieve with a 0.05 weight.";
const REPO = " Repo: /Users/javier.cabrera/DEV/hippocampus.";

const longBriefs = [
  "Goal: count the JSON files in monitoring/grafana/provisioning/dashboards.",
  "Goal: tell which of server/services/*.py is the largest by line count.",
  "Goal: list the compose services defined in docker-compose.yaml.",
  "Goal: find the default value of MEM0_RETRIEVAL_DECAY_WEIGHT in docker-compose.yaml.",
  "Goal: say how many tests files exist under server/tests.",
  "Goal: report the Grafana port published in docker-compose.yaml.",
].map((goal) => BOILER + goal + REPO + CTX + TAIL);

const mediumBriefs = [
  "How many dashboards are provisioned for Grafana in this repo?",
  "Which file defines the HeuristicReranker?",
  "What half-life do untyped memories use for decay?",
  "Where are query_event logs emitted?",
  "What does the grafana-pg-init service do?",
  "Which endpoint applies recency decay?",
].map((question) => question + REPO + TAIL);

const shortPrompts = [
  "what is my name ?",
  "fix that",
  "ok do it",
  "dashboards?",
  "why is decay not working",
  "continue",
].map((prompt) => prompt + TAIL);

function wave(label, tasks) {
  return tasks.map((task, index) => ({ key: `${label}-${index + 1}`, agent: "scout", task,
    options: { timeoutMs: 180000, toolBudget: { soft: 2, hard: 3 } } }));
}

const first = await runs.all(wave("long", longBriefs));
const second = await runs.all(wave("medium", mediumBriefs));
const third = await runs.all(wave("short", shortPrompts));
const all = first.concat(second, third);
return all.map((result, index) => ({ n: index + 1, ok: result.ok, runId: result.runId, out: String(result.output || "").slice(0, 120) }));
