from __future__ import annotations

from pathlib import Path
from typing import Any

from internal_assistant.chat.service import ChatService
from internal_assistant.observability.usage import metering
from internal_assistant.rag import RetrievalConfig

from evaluation.cost import build_cost_section, merge_cost_phases, render_cost_markdown
from evaluation.runners.common import build_worst_cases, select_examples
from evaluation.runners.run_ablation_eval import run_ablation_eval
from evaluation.runners.run_answer_eval import run_answer_eval
from evaluation.runners.run_retrieval_eval import run_retrieval_eval
from evaluation.metrics.retrieval_metrics import RETRIEVAL_STAGES, summarize_diagnosis
from evaluation.utils import load_questions, write_report_bundle


def _compute_adversarial_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    def rate(category: str) -> float:
        subset = [row for row in rows if row.get("category") == category]
        if not subset:
            return 0.0
        return sum(1.0 if row.get("answer_expected_behavior_match") else 0.0 for row in subset) / len(subset)

    return {
        "injection_rejection_rate": rate("prompt_injection"),
        "secret_request_rejection_rate": rate("secret_request"),
        "out_of_scope_rejection_rate": rate("out_of_scope"),
        "hallucination_request_rejection_rate": rate("hallucination_request"),
    }


def render_diagnosis_markdown(retrieval_rows: list[dict[str, Any]], answer_rows: list[dict[str, Any]]) -> str:
    """Donde se pierde la fuente esperada: candidatos -> fusion/reranker -> contexto (top 16) -> top 5."""
    retrieval = summarize_diagnosis(retrieval_rows)
    answer = summarize_diagnosis(answer_rows)
    labels = {
        "not_in_candidates": "No llega a candidatos (ni vector ni BM25)",
        "dropped_before_context": "Se pierde en fusion/reranker (no entra en el contexto)",
        "in_context_rank_6_16": "En el contexto, puesto 6-16",
        "in_top_5": "En el top 5",
    }
    lines = ["\n## Retrieval diagnosis\n", "| etapa | retrieval (pregunta tal cual) | answer (con planner) |", "| --- | --- | --- |"]
    for stage in RETRIEVAL_STAGES:
        lines.append(f"| {labels[stage]} | {retrieval[stage]} | {answer[stage]} |")
    return "\n".join(lines) + "\n"


def run_full_eval(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    provider_name: str = "mock",
    retrieval_config: RetrievalConfig | None = None,
    include_adversarial: bool = False,
    include_ablation: bool = False,
    use_llm_judge: bool = False,
    max_concurrency: int | None = None,
    adversarial_dataset_path: str | Path | None = None,
    session=None,
    write_reports: bool = True,
    service_class=ChatService,
) -> dict[str, Any]:
    effective_config = (retrieval_config or RetrievalConfig()).normalized()

    # Las fases anidan sus propios medidores y propagan el uso a este.
    with metering() as meter:
        print("[eval] fase 1/3: retrieval primary (embeddings + DB, no LM Studio)", flush=True)
        retrieval_report = run_retrieval_eval(
            dataset_path=dataset_path,
            output_dir=output_dir,
            provider_name=provider_name,
            retrieval_config=effective_config,
            session=session,
            questions=None,
            max_concurrency=max_concurrency,
            write_reports=False,
            service_class=service_class,
        )
        print("[eval] fase 1/3 completada", flush=True)

        print("[eval] fase 2/3: answer primary (chat + judge si esta activo)", flush=True)
        answer_report = run_answer_eval(
            dataset_path=dataset_path,
            output_dir=output_dir,
            provider_name=provider_name,
            retrieval_config=effective_config,
            session=session,
            questions=None,
            use_llm_judge=use_llm_judge,
            max_concurrency=max_concurrency,
            write_reports=False,
            service_class=service_class,
        )
        print("[eval] fase 2/3 completada", flush=True)

        adversarial_report = None
        adversarial_metrics: dict[str, float] = {}
        if include_adversarial and adversarial_dataset_path:
            print("[eval] fase 3/3: answer adversarial", flush=True)
            adversarial_report = run_answer_eval(
                dataset_path=adversarial_dataset_path,
                output_dir=output_dir,
                provider_name=provider_name,
                retrieval_config=effective_config,
                session=session,
                questions=None,
                use_llm_judge=use_llm_judge,
                max_concurrency=max_concurrency,
                write_reports=False,
                service_class=service_class,
            )
            adversarial_metrics = _compute_adversarial_metrics(adversarial_report["per_question_results"])
            print("[eval] fase 3/3 completada", flush=True)

        ablation_report = None
        if include_ablation:
            ablation_report = run_ablation_eval(
                dataset_path=dataset_path,
                output_dir=output_dir,
                provider_name=provider_name,
                session=session,
                questions=load_questions(dataset_path),
                include_answer_eval=True,
                write_reports=False,
                service_class=service_class,
            )

    answer_rows = list(answer_report["per_question_results"])
    if adversarial_report:
        answer_rows.extend(adversarial_report["per_question_results"])

    cost = merge_cost_phases(
        build_cost_section(
            meter,
            turns=len(answer_rows),
            scenarios=len({row.get("scenario_id") for row in answer_rows}),
        ),
        {
            "retrieval": retrieval_report["cost"],
            "answer": answer_report["cost"],
            "adversarial": adversarial_report["cost"] if adversarial_report else None,
            "ablation": ablation_report["cost"] if ablation_report else None,
        },
    )

    summary = {
        "Questions evaluated": len(answer_report["per_question_results"]),
        "Retrieval hit@5": round(float(retrieval_report["aggregate_metrics"]["hit_at_5"]), 4),
        "Retrieval hit@16": round(float(retrieval_report["aggregate_metrics"].get("hit_at_16", 0.0)), 4),
        "Answer behavior match": round(float(answer_report["aggregate_metrics"]["answer"]["answer_expected_behavior_match"]), 4),
        "MRR": round(float(retrieval_report["aggregate_metrics"]["mrr"]), 4),
        "Citation coverage": round(float(answer_report["aggregate_metrics"]["citation"]["citation_coverage_rate"]), 4),
        "Abstention precision": round(float(answer_report["aggregate_metrics"]["abstention"]["abstention_precision"]), 4),
        "Abstention recall": round(float(answer_report["aggregate_metrics"]["abstention"]["abstention_recall"]), 4),
        "Prompt injection rejection rate": round(float(adversarial_metrics.get("injection_rejection_rate", 0.0)), 4),
        "Average latency": round(float(answer_report["latency"]["average_latency_ms"]), 2),
        "Total cost USD": cost["total_usd"],
    }

    recommendations = list(answer_report["recommendations"])
    if ablation_report and ablation_report["recommendations"]:
        recommendations.extend(ablation_report["recommendations"][:1])

    payload = {
        "summary": summary,
        "config": {
            "provider": provider_name,
            "dataset_path": str(dataset_path),
            "adversarial_dataset_path": str(adversarial_dataset_path) if adversarial_dataset_path else "",
            "include_adversarial": include_adversarial,
            "include_ablation": include_ablation,
            "use_llm_judge": use_llm_judge,
            "top_k": effective_config.top_k,
            "vector_weight": effective_config.vector_weight,
            "text_weight": effective_config.text_weight,
        },
        "datasets": {
            "primary": str(dataset_path),
            "adversarial": str(adversarial_dataset_path) if adversarial_dataset_path else "",
        },
        "aggregate_metrics": {
            "retrieval": retrieval_report["aggregate_metrics"],
            "answer": answer_report["aggregate_metrics"]["answer"],
            "citation": answer_report["aggregate_metrics"]["citation"],
            "abstention": answer_report["aggregate_metrics"]["abstention"],
            "adversarial": adversarial_metrics,
            "ablation": ablation_report["aggregate_metrics"] if ablation_report else {},
        },
        "per_question_results": answer_rows,
        "worst_cases": build_worst_cases(answer_rows),
        "recommendations": recommendations,
        "latency": answer_report["latency"],
        "cost": cost,
    }

    if not write_reports:
        return payload

    json_path, md_path = write_report_bundle(
        report_prefix="rag_full_eval",
        output_dir=output_dir,
        payload=payload,
        markdown="",
    )
    markdown = "# RAG Evaluation Report\n\n## Summary\n"
    for key, value in summary.items():
        markdown += f"- {key}: {value}\n"
    markdown += "\n" + render_cost_markdown(cost)
    markdown += render_diagnosis_markdown(retrieval_report["per_question_results"], answer_report["per_question_results"])
    markdown += "\n## Worst Cases\n"
    markdown += "| question_id | question | expected_behavior | actual_behavior | issue | retrieved_sources |\n"
    markdown += "| --- | --- | --- | --- | --- | --- |\n"
    for row in payload["worst_cases"]:
        markdown += (
            f"| {row['question_id']} | {row['question'].replace('|', '/')} | {row['expected_behavior']} | "
            f"{row['actual_behavior']} | {row['issue'].replace('|', '/')} | {', '.join(row['retrieved_sources'])} |\n"
        )
    markdown += "\n## Correct Examples\n"
    for row in select_examples(answer_rows, correct=True):
        markdown += f"- `{row['question_id']}` {row['question']}\n"
    markdown += "\n## Problematic Examples\n"
    for row in select_examples(answer_rows, correct=False):
        markdown += f"- `{row['question_id']}` {row['question']}: {row['issue']}\n"
    markdown += "\n## Recommendations\n"
    for item in recommendations:
        markdown += f"- {item}\n"
    md_path.write_text(markdown, encoding="utf-8")
    payload["report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload
