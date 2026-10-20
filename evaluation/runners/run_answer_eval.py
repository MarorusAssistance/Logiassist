from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from internal_assistant.config import get_settings
from internal_assistant.chat.service import ChatService
from internal_assistant.db import session_scope
from internal_assistant.observability.usage import UsageMeter, metering
from internal_assistant.rag import RetrievalConfig

from evaluation.cost import build_cost_section, load_pricing, render_cost_markdown, usage_row_fields
from evaluation.judges import HeuristicJudge, LLMJudge
from evaluation.metrics.retrieval_metrics import diagnose_retrieval
from evaluation.metrics import compute_abstention_metrics, compute_answer_metrics, compute_citation_metrics
from evaluation.runners.common import answer_recommendations, build_worst_cases, retrieval_config_to_dict, select_examples
from evaluation.utils import (
    build_eval_request,
    expand_question,
    load_questions,
    select_provider,
    write_report_bundle,
)


def _ordered_retrieved_source_ids(retrieved) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in retrieved:
        key = f"{item.source_type}:{item.source_id}"
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _evaluate_scenario(
    *,
    service,
    evaluator,
    question,
    index: int,
    retrieval_config: RetrievalConfig,
    pricing: dict[str, Any],
    log: bool = False,
) -> list[dict[str, Any]]:
    """Ejecuta todos los turnos de un escenario. El uso se registra en el medidor activo."""
    rows: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    conversation_id = 10_000 + index

    for turn in expand_question(question):
        if log:
            print(f"[eval][answer] turn {turn.turn_id}", flush=True)
        # Medidor por turno: se propaga al medidor del escenario/ejecucion al salir.
        with metering() as turn_meter:
            request = build_eval_request(turn, conversation_id=conversation_id, user_id=f"eval-user-{index}")
            response, state, meta = service.simulate_chat(
                request,
                state=state,
                retrieval_config=retrieval_config,
                trace_metadata={"eval_scenario_id": turn.scenario_id, "eval_turn_id": turn.turn_id},
            )
            verdict = evaluator.judge(turn, response, meta)

        row = {
            "scenario_id": turn.scenario_id,
            "turn_id": turn.turn_id,
            "question_id": turn.turn_id,
            "question": turn.message,
            "category": turn.category,
            "expected_behavior": turn.expected_behavior,
            "expected_source_ids": list(turn.expected_source_ids),
            "expected_source_types": list(turn.expected_source_types),
            "expected_answer_summary": turn.expected_answer_summary,
            "requires_clarification": turn.requires_clarification,
            "should_create_incident": turn.should_create_incident,
            "answer": response.answer,
            "sources": [item.model_dump() for item in response.sources],
            "retrieved_source_ids": _ordered_retrieved_source_ids(meta.get("retrieved", [])),
            "retrieved_chunk_ids": [item.chunk_id for item in meta.get("retrieved", [])],
            "retrieval_diagnosis": diagnose_retrieval(list(turn.expected_source_ids), meta.get("retrieval_debug")),
            "latency_ms": meta.get("latency_ms", 0),
            "request_id": meta.get("request_id"),
            **usage_row_fields(turn_meter, pricing),
            **verdict,
        }
        row.setdefault("issue", "")
        rows.append(row)
    return rows


def _evaluate_question(
    *,
    question,
    index: int,
    provider_name: str,
    retrieval_config: RetrievalConfig,
    use_llm_judge: bool,
    service_class,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    # Los contextvars no se propagan a los hilos del pool: cada worker abre su
    # propio medidor y lo devuelve para combinarlo en el hilo principal.
    with session_scope() as session, metering(propagate=False) as meter:
        llm_provider = select_provider(provider_name)
        service = service_class(session, llm_provider=llm_provider)
        evaluator = LLMJudge(session, llm_provider=llm_provider) if use_llm_judge else HeuristicJudge(session)
        rows = _evaluate_scenario(
            service=service,
            evaluator=evaluator,
            question=question,
            index=index,
            retrieval_config=retrieval_config,
            pricing=pricing,
        )
    return {"index": index, "rows": rows, "meter": meter}


def _summarize_answer_rows(
    rows: list[dict[str, Any]],
    *,
    meter: UsageMeter,
    scenarios: int,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    answer_metrics = compute_answer_metrics(rows)
    citation_metrics = compute_citation_metrics(rows)
    abstention_metrics = compute_abstention_metrics(rows)
    average_latency_ms = (
        sum(float(row.get("latency_ms", 0.0)) for row in rows) / len(rows)
        if rows
        else 0.0
    )
    cost = build_cost_section(meter, turns=len(rows), scenarios=scenarios, pricing=pricing)
    return {
        "rows": rows,
        "aggregate_metrics": {
            "answer": answer_metrics,
            "citation": citation_metrics,
            "abstention": abstention_metrics,
        },
        "summary": {
            "Questions evaluated": len(rows),
            "Behavior match": round(float(answer_metrics["answer_expected_behavior_match"]), 4),
            "Citation coverage": round(float(citation_metrics["citation_coverage_rate"]), 4),
            "Abstention precision": round(float(abstention_metrics["abstention_precision"]), 4),
            "Abstention recall": round(float(abstention_metrics["abstention_recall"]), 4),
            "Average latency ms": round(float(average_latency_ms), 2),
            "Total cost USD": cost["total_usd"],
        },
        "latency": {"average_latency_ms": average_latency_ms},
        "cost": cost,
        "recommendations": answer_recommendations(answer_metrics, citation_metrics, abstention_metrics),
    }


def evaluate_answers(
    session,
    questions,
    *,
    provider_name: str = "",
    llm_provider,
    retrieval_config: RetrievalConfig,
    use_llm_judge: bool = False,
    judge=None,
    service_class=ChatService,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_pricing = pricing or load_pricing()
    service = service_class(session, llm_provider=llm_provider)
    evaluator = judge or (LLMJudge(session, llm_provider=llm_provider) if use_llm_judge else HeuristicJudge(session))
    rows: list[dict[str, Any]] = []

    with metering() as meter:
        for index, question in enumerate(questions, start=1):
            print(f"[eval][answer] scenario {index}/{len(questions)} {question.id}", flush=True)
            rows.extend(
                _evaluate_scenario(
                    service=service,
                    evaluator=evaluator,
                    question=question,
                    index=index,
                    retrieval_config=retrieval_config,
                    pricing=current_pricing,
                    log=True,
                )
            )

    return _summarize_answer_rows(rows, meter=meter, scenarios=len(questions), pricing=current_pricing)


def run_answer_eval(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    provider_name: str = "mock",
    retrieval_config: RetrievalConfig | None = None,
    session=None,
    questions=None,
    use_llm_judge: bool = False,
    max_concurrency: int | None = None,
    write_reports: bool = True,
    service_class=ChatService,
) -> dict[str, Any]:
    effective_config = (retrieval_config or RetrievalConfig()).normalized()
    loaded_questions = questions or load_questions(dataset_path)
    owns_session = session is None
    effective_concurrency = max(1, max_concurrency or get_settings().eval_max_concurrency)
    pricing = load_pricing()

    if owns_session and effective_concurrency > 1:
        with metering() as run_meter:
            with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
                futures = [
                    executor.submit(
                        _evaluate_question,
                        question=question,
                        index=index,
                        provider_name=provider_name,
                        retrieval_config=effective_config,
                        use_llm_judge=use_llm_judge,
                        service_class=service_class,
                        pricing=pricing,
                    )
                    for index, question in enumerate(loaded_questions, start=1)
                ]
                results = [future.result() for future in as_completed(futures)]
            results.sort(key=lambda item: item["index"])
            ordered_rows: list[dict[str, Any]] = []
            for item in results:
                ordered_rows.extend(item["rows"])
                run_meter.merge(item["meter"])
        result = _summarize_answer_rows(ordered_rows, meter=run_meter, scenarios=len(loaded_questions), pricing=pricing)
    else:
        provider = select_provider(provider_name)
        evaluate_kwargs = {
            "provider_name": provider_name,
            "llm_provider": provider,
            "retrieval_config": effective_config,
            "use_llm_judge": use_llm_judge,
            "service_class": service_class,
            "pricing": pricing,
        }
        if owns_session:
            with session_scope() as managed_session:
                result = evaluate_answers(managed_session, loaded_questions, **evaluate_kwargs)
        else:
            result = evaluate_answers(session, loaded_questions, **evaluate_kwargs)

    payload = {
        "summary": result["summary"],
        "config": {
            "provider": provider_name,
            "dataset_path": str(dataset_path),
            "use_llm_judge": use_llm_judge,
            **retrieval_config_to_dict(effective_config),
        },
        "datasets": {"primary": str(dataset_path)},
        "aggregate_metrics": result["aggregate_metrics"],
        "per_question_results": result["rows"],
        "worst_cases": build_worst_cases(result["rows"]),
        "recommendations": result["recommendations"],
        "latency": result["latency"],
        "cost": result["cost"],
    }

    if not write_reports:
        return payload

    json_path, md_path = write_report_bundle(
        report_prefix="answer_eval",
        output_dir=output_dir,
        payload=payload,
        markdown="",
    )
    report_markdown = (
        "# Answer Evaluation Report\n\n"
        "## Summary\n"
        f"- Questions evaluated: {result['summary']['Questions evaluated']}\n"
        f"- Behavior match: {result['summary']['Behavior match']}\n"
        f"- Citation coverage: {result['summary']['Citation coverage']}\n"
        f"- Abstention precision: {result['summary']['Abstention precision']}\n"
        f"- Abstention recall: {result['summary']['Abstention recall']}\n"
        f"- Average latency: {result['summary']['Average latency ms']} ms\n\n"
        + render_cost_markdown(result["cost"])
        + "\n## Worst Cases\n"
    )
    worst_cases = build_worst_cases(result["rows"])
    report_markdown += "| question_id | question | expected_behavior | actual_behavior | issue | retrieved_sources |\n"
    report_markdown += "| --- | --- | --- | --- | --- | --- |\n"
    for row in worst_cases:
        report_markdown += (
            f"| {row['question_id']} | {row['question'].replace('|', '/')} | {row['expected_behavior']} | "
            f"{row['actual_behavior']} | {row['issue'].replace('|', '/')} | {', '.join(row['retrieved_sources'])} |\n"
        )
    report_markdown += "\n## Correct Examples\n"
    for row in select_examples(result["rows"], correct=True):
        report_markdown += f"- `{row['question_id']}` {row['question']}\n"
    report_markdown += "\n## Problematic Examples\n"
    for row in select_examples(result["rows"], correct=False):
        report_markdown += f"- `{row['question_id']}` {row['question']}: {row['issue']}\n"
    report_markdown += "\n## Recommendations\n"
    for item in result["recommendations"]:
        report_markdown += f"- {item}\n"
    md_path.write_text(report_markdown, encoding="utf-8")

    payload["report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload
