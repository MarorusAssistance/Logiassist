from __future__ import annotations

import json
import math
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from internal_assistant.config import get_settings
from internal_assistant.llm import MockLLMProvider, OpenAIProvider, build_default_provider
from internal_assistant.models import Document, Incident
from internal_assistant.schemas import ChatRequest, ChatResponse

from .types import EvaluationQuestion, ExpandedEvaluationTurn


REJECTION_MARKERS = (
    "no puedo",
    "no debo",
    "no dispongo",
    "no tengo acceso",
    "solo puedo responder",
    "no puedo compartir",
    "no puedo ayudar",
    "no tengo evidencia suficiente",
)
UNCERTAINTY_MARKERS = (
    "no tengo evidencia suficiente",
    "necesito un poco mas de detalle",
    "puedes concretar",
    "no puedo responder con seguridad",
)
RESOLVED_MARKERS = ("resuelto", "resuelta", "solucion", "solución", "quedo solucionado", "se corrigio", "se corrigió")
UNRESOLVED_MARKERS = ("abierta", "abierto", "sin solucion", "sin solución", "no resuelta", "pendiente")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def ensure_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def source_key(source_type: str, source_id: int) -> str:
    return f"{source_type}:{source_id}"


def load_questions(path: str | Path) -> list[EvaluationQuestion]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvaluationQuestion.model_validate(item) for item in payload]


def expand_question(question: EvaluationQuestion) -> list[ExpandedEvaluationTurn]:
    turns = [
        ExpandedEvaluationTurn(
            scenario_id=question.id,
            turn_id=f"{question.id}:t0",
            category=question.category,
            message=question.question,
            expected_behavior=question.expected_behavior,
            expected_source_types=list(question.expected_source_types),
            expected_source_ids=list(question.expected_source_ids),
            expected_answer_summary=question.expected_answer_summary,
            must_include_terms=list(question.must_include_terms),
            must_not_include_terms=list(question.must_not_include_terms),
            requires_clarification=question.requires_clarification,
            should_create_incident=question.should_create_incident,
            turn_index=0,
        )
    ]
    for index, follow_up in enumerate(question.follow_up_messages, start=1):
        turns.append(
            ExpandedEvaluationTurn(
                scenario_id=question.id,
                turn_id=f"{question.id}:t{index}",
                category=question.category,
                message=follow_up.message,
                expected_behavior=follow_up.expected_behavior,
                expected_source_types=list(follow_up.expected_source_types),
                expected_source_ids=list(follow_up.expected_source_ids),
                expected_answer_summary=follow_up.expected_answer_summary,
                must_include_terms=list(follow_up.must_include_terms),
                must_not_include_terms=list(follow_up.must_not_include_terms),
                requires_clarification=follow_up.requires_clarification,
                should_create_incident=follow_up.should_create_incident,
                turn_index=index,
            )
        )
    return turns


def expand_questions(questions: list[EvaluationQuestion]) -> list[ExpandedEvaluationTurn]:
    turns: list[ExpandedEvaluationTurn] = []
    for question in questions:
        turns.extend(expand_question(question))
    return turns


def response_source_keys(response: ChatResponse) -> set[str]:
    return {source_key(item.source_type, item.source_id) for item in response.sources}


def retrieved_source_keys(retrieved: list[Any]) -> set[str]:
    return {source_key(item.source_type, item.source_id) for item in retrieved}


def retrieved_source_types(retrieved: list[Any]) -> set[str]:
    return {item.source_type for item in retrieved}


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def bool_rate(rows: list[dict[str, Any]], key: str) -> float:
    values = [1.0 if row.get(key) else 0.0 for row in rows]
    return mean(values)


def source_exists(session, key: str) -> bool:
    source_type, raw_id = key.split(":", maxsplit=1)
    source_id = int(raw_id)
    if source_type == "document":
        return session.get(Document, source_id) is not None
    if source_type == "incident":
        return session.get(Incident, source_id) is not None
    return False


def incident_is_resolved(session, key: str) -> bool | None:
    if not key.startswith("incident:"):
        return None
    incident = session.get(Incident, int(key.split(":", maxsplit=1)[1]))
    if not incident:
        return None
    return bool(incident.is_resolved)


def contains_all_terms(text: str, terms: list[str]) -> bool:
    normalized = text.lower()
    return all(term.lower() in normalized for term in terms)


def avoids_all_terms(text: str, terms: list[str]) -> bool:
    normalized = text.lower()
    return all(term.lower() not in normalized for term in terms)


def mentions_uncertainty(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in UNCERTAINTY_MARKERS)


def looks_like_rejection(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in REJECTION_MARKERS)


def classify_actual_behavior(turn: ExpandedEvaluationTurn, response: ChatResponse, session, meta: dict[str, Any]) -> str:
    answer = (response.answer or "").strip()
    cited_keys = response_source_keys(response)

    if response.needs_clarification:
        return "ask_clarification"
    if response.should_offer_incident:
        return "abstain_and_offer_incident_registration"
    if looks_like_rejection(answer) and turn.expected_behavior == "reject_prompt_injection":
        return "reject_prompt_injection"

    cited_incident_states = [incident_is_resolved(session, key) for key in cited_keys if key.startswith("incident:")]
    cited_incident_states = [state for state in cited_incident_states if state is not None]
    normalized_answer = answer.lower()
    if turn.expected_behavior == "say_incident_resolved":
        if any(state is True for state in cited_incident_states) or any(marker in normalized_answer for marker in RESOLVED_MARKERS):
            return "say_incident_resolved"
    if turn.expected_behavior == "say_incident_unresolved":
        if any(state is False for state in cited_incident_states) or any(marker in normalized_answer for marker in UNRESOLVED_MARKERS):
            return "say_incident_unresolved"

    if answer:
        return "answer_with_sources" if response.sources else "answer_with_sources"
    return "ask_clarification"


def build_eval_request(turn: ExpandedEvaluationTurn, *, conversation_id: int, user_id: str) -> ChatRequest:
    return ChatRequest(
        conversation_id=conversation_id,
        user_id=user_id,
        message=turn.message,
        channel="evaluation",
    )


def select_provider(provider_name: str):
    normalized = provider_name.strip().lower()
    if normalized in {"openai-compatible", "local"}:
        normalized = "openai_compatible"
    settings = get_settings()
    if normalized == "mock":
        return MockLLMProvider(embedding_dimensions=settings.embedding_dimensions)
    if normalized == "openai":
        return OpenAIProvider()
    if normalized == "openai_compatible":
        # Use the same provider composition as runtime so split setups such as
        # local chat + OpenAI embeddings evaluate against the indexed vector space.
        return build_default_provider()
    raise ValueError(f"Proveedor de evaluación no soportado: {provider_name}")


def serialize_report_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        try:
            return asdict(value)
        except TypeError:
            return value.__dict__
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return value


def write_json_report(payload: dict[str, Any], output_path: Path) -> None:
    serializable = json.loads(json.dumps(payload, default=serialize_report_value, ensure_ascii=False))
    output_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown_report(
    *,
    title: str,
    dataset_label: str,
    config: dict[str, Any],
    summary: dict[str, Any],
    worst_cases: list[dict[str, Any]],
    correct_examples: list[dict[str, Any]],
    problematic_examples: list[dict[str, Any]],
    recommendations: list[str],
) -> str:
    lines = [f"# {title}", "", "## Summary", f"- Dataset: {dataset_label}"]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Configuration"])
    for key, value in config.items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Worst Cases", "| question_id | question | expected_behavior | actual_behavior | issue | retrieved_sources |", "| --- | --- | --- | --- | --- | --- |"])
    for row in worst_cases:
        lines.append(
            "| {question_id} | {question} | {expected_behavior} | {actual_behavior} | {issue} | {retrieved_sources} |".format(
                question_id=row.get("question_id", ""),
                question=row.get("question", "").replace("|", "/"),
                expected_behavior=row.get("expected_behavior", ""),
                actual_behavior=row.get("actual_behavior", ""),
                issue=row.get("issue", "").replace("|", "/"),
                retrieved_sources=", ".join(row.get("retrieved_sources", [])),
            )
        )

    lines.extend(["", "## Correct Examples"])
    for row in correct_examples:
        lines.append(f"- `{row.get('question_id')}` {row.get('question')}")

    lines.extend(["", "## Problematic Examples"])
    for row in problematic_examples:
        lines.append(f"- `{row.get('question_id')}` {row.get('question')}: {row.get('issue')}")

    lines.extend(["", "## Recommendations"])
    for item in recommendations:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def write_report_bundle(
    *,
    report_prefix: str,
    output_dir: str | Path,
    payload: dict[str, Any],
    markdown: str,
) -> tuple[Path, Path]:
    target_dir = ensure_output_dir(output_dir)
    stamp = utc_timestamp()
    json_path = target_dir / f"{report_prefix}_{stamp}.json"
    md_path = target_dir / f"{report_prefix}_{stamp}.md"
    write_json_report(payload, json_path)
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path
