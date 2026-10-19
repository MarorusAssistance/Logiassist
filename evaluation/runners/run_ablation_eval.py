from __future__ import annotations

from pathlib import Path
from typing import Any

from internal_assistant.chat.service import ChatService
from internal_assistant.observability.usage import metering

from evaluation.cost import build_cost_section, merge_cost_phases, render_cost_markdown
from evaluation.runners.common import ABLATION_CONFIGS, retrieval_recommendations
from evaluation.runners.run_answer_eval import run_answer_eval
from evaluation.runners.run_retrieval_eval import run_retrieval_eval
from evaluation.utils import load_questions
from evaluation.utils import write_report_bundle


def run_ablation_eval(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    provider_name: str = "mock",
    session=None,
    questions=None,
    include_answer_eval: bool = True,
    write_reports: bool = True,
    service_class=ChatService,
) -> dict[str, Any]:
    loaded_questions = questions or load_questions(dataset_path)
    comparison_rows: list[dict[str, Any]] = []

    phase_costs: dict[str, dict[str, Any]] = {}
    evaluated_turns = 0
    with metering() as meter:
        for config_name, retrieval_config in ABLATION_CONFIGS:
            retrieval_report = run_retrieval_eval(
                dataset_path=dataset_path,
                output_dir=output_dir,
                provider_name=provider_name,
                retrieval_config=retrieval_config,
                session=session,
                questions=loaded_questions,
                write_reports=False,
                service_class=service_class,
            )
            citation_coverage_rate = 0.0
            config_cost = float(retrieval_report["cost"]["total_usd"])
            evaluated_turns += len(retrieval_report["per_question_results"])
            if include_answer_eval:
                answer_report = run_answer_eval(
                    dataset_path=dataset_path,
                    output_dir=output_dir,
                    provider_name=provider_name,
                    retrieval_config=retrieval_config,
                    session=session,
                    questions=loaded_questions,
                    use_llm_judge=False,
                    write_reports=False,
                    service_class=service_class,
                )
                citation_coverage_rate = float(answer_report["aggregate_metrics"]["citation"]["citation_coverage_rate"])
                config_cost += float(answer_report["cost"]["total_usd"])

            metrics = retrieval_report["aggregate_metrics"]
            comparison_rows.append(
                {
                    "config_name": config_name,
                    "hit_at_1": float(metrics["hit_at_1"]),
                    "hit_at_3": float(metrics["hit_at_3"]),
                    "hit_at_5": float(metrics["hit_at_5"]),
                    "recall_at_5": float(metrics["recall_at_5"]),
                    "mrr": float(metrics["mrr"]),
                    "citation_coverage_rate": citation_coverage_rate,
                    "average_latency_ms": float(metrics["average_latency_ms"]),
                    "cost_usd": round(config_cost, 8),
                }
            )
            phase_costs[config_name] = {"total_usd": round(config_cost, 8)}

    comparison_rows.sort(
        key=lambda row: (
            row["hit_at_5"],
            row["mrr"],
            -row["average_latency_ms"],
        ),
        reverse=True,
    )
    best = comparison_rows[0] if comparison_rows else None
    cost = merge_cost_phases(
        build_cost_section(meter, turns=evaluated_turns, scenarios=len(loaded_questions) * len(ABLATION_CONFIGS)),
        phase_costs,
    )
    recommendations = []
    if best:
        recommendations.append(
            f"La configuracion recomendada es {best['config_name']} por combinar mejor hit@5, MRR y latencia."
        )
    recommendations.extend(retrieval_recommendations(comparison_rows[0] if comparison_rows else {}))

    payload = {
        "summary": {
            "Questions evaluated": len(loaded_questions),
            "Configurations compared": len(comparison_rows),
            "Best config": best["config_name"] if best else "",
        },
        "config": {
            "provider": provider_name,
            "dataset_path": str(dataset_path),
            "include_answer_eval": include_answer_eval,
        },
        "datasets": {"primary": str(dataset_path)},
        "aggregate_metrics": {"comparison": comparison_rows},
        "per_question_results": [],
        "worst_cases": [],
        "recommendations": recommendations,
        "latency": {
            "average_latency_ms": sum(row["average_latency_ms"] for row in comparison_rows) / len(comparison_rows)
            if comparison_rows
            else 0.0
        },
        "cost": cost,
    }

    if not write_reports:
        return payload

    json_path, md_path = write_report_bundle(
        report_prefix="ablation_eval",
        output_dir=output_dir,
        payload=payload,
        markdown="",
    )
    markdown = "# Retrieval Ablation Report\n\n## Summary\n"
    markdown += f"- Questions evaluated: {payload['summary']['Questions evaluated']}\n"
    markdown += f"- Configurations compared: {payload['summary']['Configurations compared']}\n"
    markdown += f"- Best config: {payload['summary']['Best config']}\n\n"
    markdown += "## Comparison\n"
    markdown += "| config_name | hit_at_1 | hit_at_3 | hit_at_5 | recall_at_5 | mrr | citation_coverage_rate | average_latency_ms | cost_usd |\n"
    markdown += "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    for row in comparison_rows:
        markdown += (
            f"| {row['config_name']} | {row['hit_at_1']:.4f} | {row['hit_at_3']:.4f} | {row['hit_at_5']:.4f} | "
            f"{row['recall_at_5']:.4f} | {row['mrr']:.4f} | {row['citation_coverage_rate']:.4f} | {row['average_latency_ms']:.2f} | {row['cost_usd']:.6f} |\n"
        )
    markdown += "\n" + render_cost_markdown(cost)
    markdown += "\n## Recommendations\n"
    for item in recommendations:
        markdown += f"- {item}\n"
    md_path.write_text(markdown, encoding="utf-8")
    payload["report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload
