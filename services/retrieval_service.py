from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .tracing import (
    current_request_id,
    stage_timer,
    trace_backend_error,
    trace_retrieval_diagnostics,
)


class RetrievalService:
    def __init__(
        self,
        *,
        lexical_candidate_limit: int = 20,
        semantic_candidate_limit: int = 20,
        rerank_shortlist_limit: int = 12,
        default_result_limit: int = 6,
        reciprocal_rank_k: int = 60,
        reranker: "HeuristicReranker | None" = None,
    ) -> None:
        self._lexical_candidate_limit = lexical_candidate_limit
        self._semantic_candidate_limit = semantic_candidate_limit
        self._rerank_shortlist_limit = rerank_shortlist_limit
        self._default_result_limit = default_result_limit
        self._reciprocal_rank_k = reciprocal_rank_k
        self._reranker = reranker or HeuristicReranker()

    def search(
        self,
        memory_instance: Any,
        *,
        query: str,
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> Any:
        params = self._search_params(
            user_id=user_id,
            run_id=run_id,
            agent_id=agent_id,
            filters=filters,
        )
        with stage_timer() as total_elapsed_ms:
            semantic_response, semantic_latency_ms, semantic_degraded = (
                self._semantic_search(
                    memory_instance,
                    query=query,
                    params=params,
                )
            )
            lexical_count, lexical_latency_ms, lexical_degraded = (
                self._lexical_diagnostics(
                    memory_instance,
                    query=query,
                    user_id=user_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    filters=filters,
                )
            )

        semantic_count = self._result_count(semantic_response)
        diagnostics = self._build_retrieval_diagnostics(
            lexical_count=lexical_count,
            semantic_count=semantic_count,
            lexical_latency_ms=lexical_latency_ms,
            semantic_latency_ms=semantic_latency_ms,
            lexical_degraded=lexical_degraded,
            semantic_degraded=semantic_degraded,
            total_latency_ms=total_elapsed_ms(),
        )
        trace_retrieval_diagnostics(
            "retrieval.search",
            diagnostics=diagnostics,
        )
        return self._attach_trace(
            semantic_response,
            diagnostics=diagnostics,
            params=params,
            query=query,
        )

    def lexical_search(
        self,
        memory_instance: Any,
        *,
        query: str,
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        results, lexical_latency_ms = self._lexical_results(
            memory_instance,
            query=query,
            user_id=user_id,
            run_id=run_id,
            agent_id=agent_id,
            filters=filters,
        )
        diagnostics = self._build_retrieval_diagnostics(
            lexical_count=len(results),
            semantic_count=0,
            lexical_latency_ms=lexical_latency_ms,
            semantic_latency_ms=0.0,
            lexical_degraded=False,
            semantic_degraded=False,
            total_latency_ms=lexical_latency_ms,
        )
        trace_retrieval_diagnostics("retrieval.lexical_search", diagnostics=diagnostics)
        return {
            "query": query,
            "params": self._search_params(
                user_id=user_id,
                run_id=run_id,
                agent_id=agent_id,
                filters=filters,
            ),
            "results": results,
            "trace": {
                "request_id": current_request_id(),
                "retrieval": diagnostics,
            },
        }

    def retrieve(
        self,
        memory_instance: Any,
        *,
        query: str,
        scopes: Sequence[str],
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        limit: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_limit = limit or self._default_result_limit
        requested_scopes = [
            scope for scope in scopes if isinstance(scope, str) and scope
        ]
        if not requested_scopes:
            raise ValueError("At least one retrieval scope is required.")

        identifier_params = self._identifier_params(
            user_id=user_id,
            run_id=run_id,
            agent_id=agent_id,
        )
        combined_filters = self._combine_filters(filters, requested_scopes)
        semantic_params = self._search_params(
            user_id=user_id,
            run_id=run_id,
            agent_id=agent_id,
            filters=filters,
        )

        with stage_timer() as total_elapsed_ms:
            lexical_results, lexical_latency_ms, lexical_reason = (
                self._safe_lexical_results(
                    memory_instance,
                    query=query,
                    identifier_params=identifier_params,
                    filters=combined_filters,
                )
            )
            semantic_results, semantic_latency_ms, semantic_reason = (
                self._safe_semantic_results(
                    memory_instance,
                    query=query,
                    params=semantic_params,
                    filters=combined_filters,
                )
            )

            if lexical_reason is not None and semantic_reason is not None:
                raise RuntimeError(
                    "Unable to retrieve memories because lexical and semantic stages both failed."
                )

            rerank_applied = False
            rerank_latency_ms = 0.0
            rerank_reason: str | None = None

            if lexical_reason is None and semantic_reason is None:
                fused_candidates = self._fuse_candidates(
                    query=query,
                    lexical_results=lexical_results,
                    semantic_results=semantic_results,
                )
                try:
                    fused_candidates, rerank_latency_ms = self._rerank_candidates(
                        query=query,
                        fused_candidates=fused_candidates,
                    )
                    rerank_applied = True
                except Exception as exc:
                    trace_backend_error("retrieval.rerank_stage", exc)
                    rerank_reason = "rerank_unavailable"
            elif lexical_reason is None:
                fused_candidates = self._fallback_candidates(
                    stage="lexical",
                    candidates=lexical_results,
                )
            else:
                fused_candidates = self._fallback_candidates(
                    stage="semantic",
                    candidates=semantic_results,
                )

        reasons = [
            reason
            for reason in (lexical_reason, semantic_reason, rerank_reason)
            if reason is not None
        ]
        if (lexical_reason is not None) ^ (semantic_reason is not None):
            reasons.append("rerank_skipped")

        backend_capabilities = {
            "lexical": lexical_reason is None,
            "semantic": semantic_reason is None,
            "rerank": rerank_applied,
            "anchors": True,
        }
        degraded = bool(reasons)
        results = self._finalize_candidates(
            fused_candidates[:normalized_limit],
            reranked=rerank_applied,
        )
        diagnostics = {
            "lexical_count": len(lexical_results),
            "semantic_count": len(semantic_results),
            "result_count": len(results),
            "rerank_applied": rerank_applied,
            "backend_capabilities": backend_capabilities,
            "degraded": {
                "lexical": lexical_reason is not None,
                "semantic": semantic_reason is not None,
                "rerank": rerank_reason is not None,
            },
            "degradation_reasons": reasons,
            "latency_ms": {
                "lexical": lexical_latency_ms,
                "semantic": semantic_latency_ms,
                "rerank": rerank_latency_ms,
                "total": total_elapsed_ms(),
            },
        }
        trace_retrieval_diagnostics(
            "retrieval.retrieve",
            diagnostics=diagnostics,
        )
        request_id = current_request_id()
        return {
            "request_id": request_id,
            "query": query,
            "results": results,
            "backend_capabilities": backend_capabilities,
            "degraded": degraded,
            "degradation_reasons": reasons,
            "trace": {
                "request_id": request_id,
                "retrieval": diagnostics,
            },
        }

    def _lexical_results(
        self,
        memory_instance: Any,
        *,
        query: str,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
        filters: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], float]:
        with stage_timer() as lexical_elapsed_ms:
            identifier_params = self._identifier_params(
                user_id=user_id,
                run_id=run_id,
                agent_id=agent_id,
            )
            results = self._collect_lexical_results(
                memory_instance,
                query=query,
                identifier_params=identifier_params,
                filters=filters,
            )
        return results, lexical_elapsed_ms()

    def _collect_lexical_results(
        self,
        memory_instance: Any,
        *,
        query: str,
        identifier_params: dict[str, Any],
        filters: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        records = self._coerce_records(memory_instance.get_all(**identifier_params))
        if filters is not None:
            records = [
                record for record in records if self._matches_filters(record, filters)
            ]
        return self._lexical_candidates(query=query, records=records)

    @staticmethod
    def _search_params(
        *,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
        filters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "user_id": user_id,
                "run_id": run_id,
                "agent_id": agent_id,
                "filters": filters,
            }.items()
            if value is not None
        }

    @staticmethod
    def _identifier_params(
        *,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "user_id": user_id,
                "run_id": run_id,
                "agent_id": agent_id,
            }.items()
            if value is not None
        }

    def _annotate_response(self, response: Any, *, stage: str, strategy: str) -> Any:
        if isinstance(response, dict):
            annotated_response = dict(response)
            results = annotated_response.get("results")
            if isinstance(results, list):
                annotated_response["results"] = [
                    self._annotate_candidate(
                        candidate,
                        stage=stage,
                        strategy=strategy,
                    )
                    for candidate in results
                ]
            return annotated_response

        if isinstance(response, list):
            return [
                self._annotate_candidate(candidate, stage=stage, strategy=strategy)
                for candidate in response
            ]

        return response

    def _attach_trace(
        self,
        response: Any,
        *,
        diagnostics: dict[str, Any],
        params: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        annotated_response = self._annotate_response(
            response,
            stage="semantic",
            strategy="semantic",
        )
        if isinstance(annotated_response, dict):
            response_payload = dict(annotated_response)
            response_payload.setdefault("query", query)
            response_payload.setdefault("params", params)
            response_payload["trace"] = {
                "request_id": current_request_id(),
                "retrieval": diagnostics,
            }
            return response_payload

        results = (
            annotated_response
            if isinstance(annotated_response, list)
            else [annotated_response]
        )
        return {
            "query": query,
            "params": params,
            "results": results,
            "trace": {
                "request_id": current_request_id(),
                "retrieval": diagnostics,
            },
        }

    def _semantic_search(
        self,
        memory_instance: Any,
        *,
        query: str,
        params: dict[str, Any],
    ) -> tuple[Any, float, bool]:
        with stage_timer() as semantic_elapsed_ms:
            try:
                response = memory_instance.search(query=query, **params)
                return response, semantic_elapsed_ms(), False
            except Exception as exc:
                trace_backend_error("retrieval.semantic_stage", exc)
                raise

    def _lexical_diagnostics(
        self,
        memory_instance: Any,
        *,
        query: str,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
        filters: dict[str, Any] | None,
    ) -> tuple[int, float, bool]:
        try:
            results, lexical_latency_ms = self._lexical_results(
                memory_instance,
                query=query,
                user_id=user_id,
                run_id=run_id,
                agent_id=agent_id,
                filters=filters,
            )
        except Exception as exc:
            trace_backend_error("retrieval.lexical_stage", exc)
            return 0, 0.0, True

        return len(results), lexical_latency_ms, False

    @staticmethod
    def _build_retrieval_diagnostics(
        *,
        lexical_count: int,
        semantic_count: int,
        lexical_latency_ms: float,
        semantic_latency_ms: float,
        lexical_degraded: bool,
        semantic_degraded: bool,
        total_latency_ms: float,
    ) -> dict[str, Any]:
        return {
            "lexical_count": lexical_count,
            "semantic_count": semantic_count,
            "rerank_applied": False,
            "degraded": {
                "lexical": lexical_degraded,
                "semantic": semantic_degraded,
                "rerank": False,
            },
            "latency_ms": {
                "lexical": lexical_latency_ms,
                "semantic": semantic_latency_ms,
                "rerank": 0.0,
                "total": total_latency_ms,
            },
        }

    @staticmethod
    def _result_count(response: Any) -> int:
        if isinstance(response, dict):
            results = response.get("results")
            return len(results) if isinstance(results, list) else 0
        if isinstance(response, list):
            return len(response)
        return 0

    def _lexical_candidates(
        self,
        *,
        query: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored_records: list[tuple[int, str, dict[str, Any]]] = []
        for record in records:
            score = self._lexical_score(query, query_tokens, record)
            if score <= 0:
                continue

            record_id = str(record.get("id", ""))
            scored_records.append(
                (
                    score,
                    record_id,
                    self._annotate_candidate(
                        record,
                        stage="lexical",
                        strategy="keyword",
                        score=score,
                    ),
                )
            )

        scored_records.sort(key=lambda item: (-item[0], item[1]))
        return [
            candidate
            for _, _, candidate in scored_records[: self._lexical_candidate_limit]
        ]

    def _lexical_score(
        self,
        query: str,
        query_tokens: list[str],
        record: dict[str, Any],
    ) -> int:
        corpus = self._record_corpus(record)
        if not corpus:
            return 0

        token_counts = Counter(self._tokenize(corpus))
        score = sum(token_counts.get(token, 0) for token in query_tokens)
        if query.strip() and query.casefold() in corpus.casefold():
            score += len(query_tokens)
        return score

    def _record_corpus(self, record: dict[str, Any]) -> str:
        fragments: list[str] = []
        self._collect_text_fragments(record.get("memory"), fragments)
        self._collect_text_fragments(record.get("messages"), fragments)
        self._collect_text_fragments(record.get("metadata"), fragments)
        return " ".join(fragment for fragment in fragments if fragment)

    def _collect_text_fragments(self, value: Any, fragments: list[str]) -> None:
        if value is None:
            return
        if isinstance(value, str):
            fragments.append(value)
            return
        if isinstance(value, (int, float, bool)):
            fragments.append(str(value))
            return
        if isinstance(value, dict):
            for key, nested_value in value.items():
                fragments.append(str(key))
                self._collect_text_fragments(nested_value, fragments)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_text_fragments(item, fragments)

    def _annotate_candidate(
        self,
        candidate: Any,
        *,
        stage: str,
        strategy: str,
        score: float | None = None,
    ) -> Any:
        if not isinstance(candidate, dict):
            return candidate

        annotated = dict(candidate)
        retrieval_metadata: dict[str, Any] = {
            "stage": stage,
            "source": "memory_store",
            "strategy": strategy,
        }
        if score is not None:
            retrieval_metadata["score"] = score
        annotated["_retrieval"] = retrieval_metadata
        return annotated

    def _coerce_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return self._coerce_records(results)
            return []

        if not isinstance(payload, list):
            return []

        records: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            nested_items = item.get("items")
            if isinstance(nested_items, list):
                records.extend(
                    nested_item
                    for nested_item in nested_items
                    if isinstance(nested_item, dict)
                )
                continue

            records.append(item)
        return records

    def _matches_filters(self, record: dict[str, Any], filters: dict[str, Any]) -> bool:
        return all(
            self._matches_filter_value(record, key, expected_value)
            for key, expected_value in filters.items()
        )

    def _matches_filter_value(
        self,
        record: dict[str, Any],
        key: str,
        expected_value: Any,
    ) -> bool:
        values_to_check = [record.get(key)]
        metadata = record.get("metadata")
        if isinstance(metadata, dict):
            values_to_check.append(metadata.get(key))
        return any(
            self._values_match(value, expected_value) for value in values_to_check
        )

    def _values_match(self, value: Any, expected_value: Any) -> bool:
        if isinstance(expected_value, list):
            if isinstance(value, list):
                return any(
                    self._values_match(item, item_expected)
                    for item in value
                    for item_expected in expected_value
                )
            return any(self._values_match(value, item) for item in expected_value)
        if isinstance(value, list):
            return any(self._values_match(item, expected_value) for item in value)
        if isinstance(expected_value, dict):
            return isinstance(value, dict) and all(
                self._values_match(value.get(key), nested_expected)
                for key, nested_expected in expected_value.items()
            )
        return value == expected_value

    def _safe_lexical_results(
        self,
        memory_instance: Any,
        *,
        query: str,
        identifier_params: dict[str, Any],
        filters: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], float, str | None]:
        with stage_timer() as lexical_elapsed_ms:
            try:
                results = self._collect_lexical_results(
                    memory_instance,
                    query=query,
                    identifier_params=identifier_params,
                    filters=filters,
                )
            except Exception as exc:
                trace_backend_error("retrieval.lexical_stage", exc)
                return [], lexical_elapsed_ms(), "lexical_unavailable"
        return results, lexical_elapsed_ms(), None

    def _safe_semantic_results(
        self,
        memory_instance: Any,
        *,
        query: str,
        params: dict[str, Any],
        filters: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], float, str | None]:
        with stage_timer() as semantic_elapsed_ms:
            try:
                response = memory_instance.search(query=query, **params)
            except Exception as exc:
                trace_backend_error("retrieval.semantic_stage", exc)
                return [], semantic_elapsed_ms(), "semantic_unavailable"

            records = self._coerce_records(response)
            if filters is not None:
                records = [
                    record
                    for record in records
                    if self._matches_filters(record, filters)
                ]
            semantic_results = [
                self._annotate_candidate(
                    candidate,
                    stage="semantic",
                    strategy="semantic",
                    score=self._semantic_score(candidate),
                )
                for candidate in records[: self._semantic_candidate_limit]
            ]
        return semantic_results, semantic_elapsed_ms(), None

    def _combine_filters(
        self,
        filters: dict[str, Any] | None,
        scopes: Sequence[str],
    ) -> dict[str, Any] | None:
        combined_filters = dict(filters or {})
        if "scope" not in combined_filters:
            combined_filters["scope"] = list(scopes) if len(scopes) > 1 else scopes[0]
        return combined_filters or None

    def _semantic_score(self, candidate: dict[str, Any]) -> float | None:
        raw_score = candidate.get("score")
        if isinstance(raw_score, (int, float)):
            return round(float(raw_score), 6)
        retrieval_metadata = candidate.get("_retrieval")
        if isinstance(retrieval_metadata, dict):
            nested_score = retrieval_metadata.get("score")
            if isinstance(nested_score, (int, float)):
                return round(float(nested_score), 6)
        return None

    def _fuse_candidates(
        self,
        *,
        query: str,
        lexical_results: list[dict[str, Any]],
        semantic_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}

        for rank, candidate in enumerate(lexical_results, start=1):
            candidate_id = self._candidate_key(candidate, rank, "lexical")
            entry = candidate_map.setdefault(
                candidate_id,
                self._new_candidate_entry(candidate_id=candidate_id),
            )
            entry["id"] = candidate.get("id", candidate_id)
            entry["lexical"] = candidate
            entry["lexical_rank"] = rank
            entry["lexical_score"] = self._coerce_numeric(
                candidate.get("_retrieval", {}).get("score")
                if isinstance(candidate.get("_retrieval"), dict)
                else None
            )
            entry["record"] = self._merge_records(entry.get("record"), candidate)

        for rank, candidate in enumerate(semantic_results, start=1):
            candidate_id = self._candidate_key(candidate, rank, "semantic")
            entry = candidate_map.setdefault(
                candidate_id,
                self._new_candidate_entry(candidate_id=candidate_id),
            )
            entry["id"] = candidate.get("id", candidate_id)
            entry["semantic"] = candidate
            entry["semantic_rank"] = rank
            entry["semantic_score"] = self._semantic_score(candidate)
            entry["record"] = self._merge_records(entry.get("record"), candidate)

        for entry in candidate_map.values():
            lexical_rank = entry["lexical_rank"]
            semantic_rank = entry["semantic_rank"]
            fused_score = 0.0
            if lexical_rank is not None:
                fused_score += 1.0 / (self._reciprocal_rank_k + lexical_rank)
            if semantic_rank is not None:
                fused_score += 1.0 / (self._reciprocal_rank_k + semantic_rank)
            entry["fused_score"] = round(fused_score, 6)

        sorted_entries = sorted(candidate_map.values(), key=self._fusion_sort_key)
        return [
            self._entry_to_result(entry=entry, strategy="fusion")
            for entry in sorted_entries
        ]

    def _rerank_candidates(
        self,
        *,
        query: str,
        fused_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], float]:
        with stage_timer() as rerank_elapsed_ms:
            shortlist = fused_candidates[: self._rerank_shortlist_limit]
            reranked_shortlist = self._reranker.rerank(
                query=query, candidates=shortlist
            )
            reranked_results = (
                reranked_shortlist + fused_candidates[self._rerank_shortlist_limit :]
            )
        return reranked_results, rerank_elapsed_ms()

    def _fallback_candidates(
        self,
        *,
        stage: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fallback_results: list[dict[str, Any]] = []
        for position, candidate in enumerate(candidates, start=1):
            matched_by = [stage]
            stage_score = (
                self._semantic_score(candidate)
                if stage == "semantic"
                else self._coerce_numeric(
                    candidate.get("_retrieval", {}).get("score")
                    if isinstance(candidate.get("_retrieval"), dict)
                    else None
                )
            )
            fallback_results.append(
                self._build_result_payload(
                    base_record=candidate,
                    matched_by=matched_by,
                    lexical_score=stage_score if stage == "lexical" else None,
                    semantic_score=stage_score if stage == "semantic" else None,
                    score=stage_score,
                    strategy="keyword" if stage == "lexical" else "semantic",
                    reranked=False,
                    rank_position=position,
                )
            )
        return fallback_results

    def _finalize_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        reranked: bool,
    ) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for position, candidate in enumerate(candidates, start=1):
            finalized_candidate = dict(candidate)
            retrieval_metadata = dict(finalized_candidate.get("retrieval", {}))
            retrieval_metadata["reranked"] = reranked
            retrieval_metadata["rank_position"] = position
            finalized_candidate["retrieval"] = retrieval_metadata

            source_metadata = dict(finalized_candidate.get("_retrieval", {}))
            source_metadata["reranked"] = reranked
            source_metadata["rank_position"] = position
            finalized_candidate["_retrieval"] = source_metadata
            finalized.append(finalized_candidate)
        return finalized

    def _entry_to_result(
        self,
        *,
        entry: dict[str, Any],
        strategy: str,
    ) -> dict[str, Any]:
        matched_by = self._matched_by(entry)
        return self._build_result_payload(
            base_record=entry["record"],
            matched_by=matched_by,
            lexical_score=entry["lexical_score"],
            semantic_score=entry["semantic_score"],
            score=entry["fused_score"],
            strategy=strategy,
            reranked=False,
            rank_position=0,
        )

    def _build_result_payload(
        self,
        *,
        base_record: dict[str, Any],
        matched_by: list[str],
        lexical_score: float | None,
        semantic_score: float | None,
        score: float | None,
        strategy: str,
        reranked: bool,
        rank_position: int,
    ) -> dict[str, Any]:
        result = dict(base_record)
        if score is not None:
            result["score"] = round(float(score), 6)

        retrieval = {
            "matched_by": matched_by,
            "lexical_score": lexical_score,
            "semantic_score": semantic_score,
            "reranked": reranked,
            "rank_position": rank_position,
        }
        result["retrieval"] = retrieval
        result["_retrieval"] = {
            "stage": matched_by[0] if len(matched_by) == 1 else "hybrid",
            "source": "memory_store",
            "strategy": strategy,
            "matched_by": matched_by,
            "lexical_score": lexical_score,
            "semantic_score": semantic_score,
            "score": result.get("score"),
            "reranked": reranked,
            "rank_position": rank_position,
        }
        return result

    @staticmethod
    def _new_candidate_entry(*, candidate_id: str) -> dict[str, Any]:
        return {
            "id": candidate_id,
            "record": {},
            "lexical": None,
            "semantic": None,
            "lexical_rank": None,
            "semantic_rank": None,
            "lexical_score": None,
            "semantic_score": None,
            "fused_score": 0.0,
        }

    @staticmethod
    def _candidate_key(candidate: dict[str, Any], rank: int, stage: str) -> str:
        candidate_id = candidate.get("id")
        if isinstance(candidate_id, str) and candidate_id:
            return candidate_id
        return f"{stage}-{rank}"

    @staticmethod
    def _merge_records(
        existing_record: dict[str, Any] | None,
        incoming_record: dict[str, Any],
    ) -> dict[str, Any]:
        merged_record = dict(existing_record or {})
        for key, value in incoming_record.items():
            if key == "_retrieval":
                continue
            if value is None and key in merged_record:
                continue
            merged_record[key] = value
        return merged_record

    @staticmethod
    def _coerce_numeric(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return round(float(value), 6)
        return None

    @staticmethod
    def _matched_by(entry: dict[str, Any]) -> list[str]:
        matches: list[str] = []
        if entry.get("lexical") is not None:
            matches.append("lexical")
        if entry.get("semantic") is not None:
            matches.append("semantic")
        return matches

    def _fusion_sort_key(self, entry: dict[str, Any]) -> tuple[Any, ...]:
        matched_by = self._matched_by(entry)
        semantic_rank = entry.get("semantic_rank")
        lexical_rank = entry.get("lexical_rank")
        recency = self._recency_value(entry.get("record", {}))
        return (
            -float(entry.get("fused_score", 0.0)),
            -int(len(matched_by) == 2),
            semantic_rank if semantic_rank is not None else float("inf"),
            lexical_rank if lexical_rank is not None else float("inf"),
            -recency,
            str(entry.get("id", "")),
        )

    @staticmethod
    def _recency_value(record: dict[str, Any]) -> float:
        for key in ("updated_at", "created_at"):
            value = record.get(key)
            if isinstance(value, str) and value:
                try:
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).timestamp()
                except ValueError:
                    continue
        return 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9_-]+", text.casefold())


class HeuristicReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query_tokens = RetrievalService._tokenize(query)
        scored_candidates: list[tuple[float, int, dict[str, Any]]] = []

        for index, candidate in enumerate(candidates, start=1):
            corpus = self._candidate_corpus(candidate)
            corpus_tokens = set(RetrievalService._tokenize(corpus))
            overlap = len([token for token in query_tokens if token in corpus_tokens])
            phrase_bonus = (
                0.02 if query.strip() and query.casefold() in corpus.casefold() else 0.0
            )
            type_bonus = self._type_bonus(candidate)
            base_score = float(candidate.get("score", 0.0) or 0.0)
            heuristic_score = round(
                base_score + (overlap * 0.005) + phrase_bonus + type_bonus, 6
            )
            reranked_candidate = dict(candidate)
            reranked_candidate["score"] = heuristic_score
            scored_candidates.append((heuristic_score, index, reranked_candidate))

        scored_candidates.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _, _, candidate in scored_candidates]

    @staticmethod
    def _candidate_corpus(candidate: dict[str, Any]) -> str:
        fragments: list[str] = []
        for key in ("memory", "content", "messages", "metadata"):
            value = candidate.get(key)
            if isinstance(value, str):
                fragments.append(value)
            elif isinstance(value, list):
                fragments.extend(str(item) for item in value)
            elif isinstance(value, dict):
                fragments.extend(str(item) for item in value.values())
        return " ".join(fragment for fragment in fragments if fragment)

    @staticmethod
    def _type_bonus(candidate: dict[str, Any]) -> float:
        metadata = candidate.get("metadata")
        memory_type = None
        if isinstance(metadata, dict):
            memory_type = metadata.get("type") or metadata.get("memory_type")
        if memory_type == "decision":
            return 0.01
        if memory_type == "stable-fact":
            return 0.005
        return 0.0
