# Recall stress experiment

Goal: see how hippocampus auto-recall behaves for subagent prompts of different shapes.

- `recall-stress.js` launches 18 read-only scouts in 3 waves of 6: long task briefs (with the `jev_route`
  boilerplate), medium questions, and short ambiguous prompts ("fix that", "continue").
- Model: `litellm/bedrock-claude-haiku-5-5` (about $0.11 per million input tokens, $0.55 output).
- Cost cap: `usageBudget.costUsd.hard = 2` stops launching later waves once reported usage passes $2.
  Expected cost is well under $1: 18 children x ~100k input tokens x $0.11/M is about $0.20.
- Per child: 180 s timeout and a hard tool budget of 3 calls.

## Read the result (last 30 minutes)

Dashboards: Query Misses (zero-hit rate by query length, true misses table) and Hot Memories.

Loki, all stress queries by length: `{service="mem0-server",event="query_event",endpoint="retrieve"} | json`.
Compare zero-hit rate and `hits` for queries under 100, 100-300 and 300+ characters.
