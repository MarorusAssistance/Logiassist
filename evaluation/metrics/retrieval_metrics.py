from __future__ import annotations

from typing import Any

from evaluation.utils import mean


def _eligible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("expected_source_ids")]


def _hit_at_k(row: dict[str, Any], k: int) -> float:
    expected = set(row.get("expected_source_ids", []))
    retrieved = list(row.get("retrieved_source_ids", []))[:k]
    return 1.0 if expected.intersection(retrieved) else 0.0


def _coverage_fraction(row: dict[str, Any], k: int) -> float:
    expected = set(row.get("expected_source_ids", []))
    if not expected:
        return 0.0
    retrieved = set(list(row.get("retrieved_source_ids", []))[:k])
    return len(expected.intersection(retrieved)) / len(expected)


def _all_expected_covered(row: dict[str, Any], k: int) -> float:
    expected = set(row.get("expected_source_ids", []))
    if not expected:
        return 0.0
    retrieved = set(list(row.get("retrieved_source_ids", []))[:k])
    return 1.0 if expected.issubset(retrieved) else 0.0


def _mrr(row: dict[str, Any]) -> float:
    expected = set(row.get("expected_source_ids", []))
    for index, source_id in enumerate(row.get("retrieved_source_ids", []), start=1):
        if source_id in expected:
            return 1.0 / index
    return 0.0


def compute_retrieval_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    eligible = _eligible_rows(rows)
    if not eligible:
        return {
            "evaluated_questions": 0,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "hit_at_5": 0.0,
            "recall_at_5": 0.0,
            "hit_at_16": 0.0,
            "mrr": 0.0,
            "average_retrieval_score": 0.0,
            "expected_source_coverage": 0.0,
            "source_type_match_rate": 0.0,
            "average_latency_ms": 0.0,
        }

    return {
        "evaluated_questions": float(len(eligible)),
        "hit_at_1": mean([_hit_at_k(row, 1) for row in eligible]),
        "hit_at_3": mean([_hit_at_k(row, 3) for row in eligible]),
        "hit_at_5": mean([_hit_at_k(row, 5) for row in eligible]),
        "recall_at_5": mean([_coverage_fraction(row, 5) for row in eligible]),
        "hit_at_16": mean([_hit_at_k(row, 16) for row in eligible]),
        "mrr": mean([_mrr(row) for row in eligible]),
        "average_retrieval_score": mean([float(row.get("top_score", 0.0)) for row in eligible]),
        "expected_source_coverage": mean([_all_expected_covered(row, 5) for row in eligible]),
        "source_type_match_rate": mean(
            [
                1.0
                if set(row.get("expected_source_types", [])).issubset(set(row.get("retrieved_source_types", [])))
                else 0.0
                for row in eligible
                if row.get("expected_source_types")
            ]
            or [0.0]
        ),
        "average_latency_ms": mean([float(row.get("latency_ms", 0.0)) for row in eligible]),
    }



RETRIEVAL_STAGES = ("not_in_candidates", "dropped_before_context", "in_context_rank_6_16", "in_top_5")


def diagnose_retrieval(expected_source_ids: list[str], debug: dict[str, Any] | None) -> dict[str, Any]:
    """Posicion (1-based) de la mejor fuente esperada en cada etapa y la etapa donde se pierde."""
    if not expected_source_ids or not debug:
        return {}
    expected = set(expected_source_ids)

    def best_rank(key: str) -> int | None:
        ranks = [index for index, source in enumerate(debug.get(key) or [], start=1) if source in expected]
        return min(ranks) if ranks else None

    ranks = {
        "vector_rank": best_rank("vector_sources"),
        "text_rank": best_rank("text_sources"),
        "fused_rank": best_rank("fused_sources"),
        "reranked_rank": best_rank("reranked_sources"),
        "returned_rank": best_rank("returned_sources"),
    }
    if ranks["vector_rank"] is None and ranks["text_rank"] is None:
        stage = "not_in_candidates"
    elif ranks["returned_rank"] is None:
        stage = "dropped_before_context"
    elif ranks["returned_rank"] > 5:
        stage = "in_context_rank_6_16"
    else:
        stage = "in_top_5"
    return {
        **ranks,
        "lost_at": stage,
        "keyword_query": debug.get("keyword_query", ""),
        "search_keywords": debug.get("search_keywords", []),
        "knowledge_query": debug.get("knowledge_query", ""),
        "retrieval_filters": debug.get("retrieval_filters", {}),
        "filter_fallback": debug.get("filter_fallback", False),
        "reranker_applied": debug.get("reranker_applied", False),
    }


def summarize_diagnosis(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {stage: 0 for stage in RETRIEVAL_STAGES}
    for row in rows:
        stage = (row.get("retrieval_diagnosis") or {}).get("lost_at")
        if stage in counts:
            counts[stage] += 1
    return counts
