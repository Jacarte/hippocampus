# Draft: mem0 mgrep

## Requirements (confirmed)
- Extend the current mem0server backend with functionality similar to mgrep.
- Include an HTTP API, a CLI tool, and OpenCode MCP configuration/integration.
- First useful version should search **memories + files/docs**.
- First version should include **full watch + query**, not just query over pre-ingested data.
- Primary interfaces should have **HTTP + CLI parity**.
- Results should be **ranked hits + optional synthesized answer**.
- Storage model should use **separate corpora** for memories and files/docs.
- V1 file scope: **code + Markdown/text docs**.
- MCP integration shape for v1: **HTTP-backed bridge**.
- V1 should include an explicit **reset/drop operation** for the file/doc corpus.

## Technical Decisions
- Preserve existing memory CRUD, `/search`, and `/retrieve` behavior as additive surfaces.
- Use a separate file/doc indexing pipeline rather than forcing files into the existing memory corpus.
- Use AST/symbol-aware chunking for code files; fallback text chunking for unsupported parses.
- Treat semantic search as complementary to grep: semantic for discovery, grep for exact verification.

## Research Findings
- Current backend already supports semantic retrieval, lexical memory-store retrieval, hybrid fusion, reranking, degradation metadata, and provenance.
- Current backend does **not** support repo-file indexing or a public lexical endpoint.
- External evidence supports hybrid workflows: semantic/code-intent search is better for discovery and onboarding; grep/ripgrep remains better for exact literals and regex.
- Code chunking best practice is structure-aware chunking using AST/symbol boundaries with rich metadata.
- Existing test infrastructure is present: pytest route/service tests already cover CRUD, `/search`, lexical retrieval, `/retrieve`, and degradation behavior.

## Scope Boundaries
- INCLUDE: new indexing pipeline, unified query API, CLI parity, MCP/OpenCode integration, watch/sync/status flows.
- EXCLUDE: replacing existing memory endpoints, full GUI, advanced multimodal indexing, cloud sync.

## Open Questions
- Should synthesized answer mode be part of v1 plan by default, or optional/deferred if model/config complexity is high?
