from __future__ import annotations

import json

from internal_assistant.llm.prompts import PLANNER_PROMPT, SECURITY_RULES, SYSTEM_PROMPT
from internal_assistant.schemas.chat import AssistantDecision, ChatPlan
from internal_assistant.security.prompt_guard import CANARY_TOKEN, DATA_TAG, new_nonce, wrap_untrusted


def render_system_prompt(template: str) -> str:
    rules = SECURITY_RULES.replace("{data_tag}", DATA_TAG).replace("{canary}", CANARY_TOKEN)
    return template.replace("{security_rules}", rules)


def build_chat_messages(
    *,
    question: str,
    context_chunks: list[dict],
    conversation_state: dict,
) -> list[dict[str, str]]:
    nonce = new_nonce()
    context = "\n\n".join(
        wrap_untrusted(
            str(item.get("content", "")),
            nonce=nonce,
            kind="fragmento_indice",
            chunk_id=item["chunk_id"],
            source=f"{item.get('source_type', '')}:{item.get('source_id', '')}",
        )
        for item in context_chunks
    )
    evidence_policy = conversation_state.get("_evidence_policy") or {}
    conversation_memory = conversation_state.get("_conversation_memory") or []
    datasource_status = conversation_state.get("_datasource_status") or {}
    planner_output = conversation_state.get("_planner_output") or {}
    memory_context = "\n\n".join(
        wrap_untrusted(
            str(item.get("memory_text", "")),
            nonce=nonce,
            kind="memoria_conversacional",
            memory_id=item.get("memory_id"),
            message_id=item.get("message_id"),
            role=item.get("role"),
            score=f"{item.get('score', 0):.4f}",
        )
        for item in conversation_memory
    )
    return [
        {"role": "system", "content": render_system_prompt(SYSTEM_PROMPT)},
        {
            "role": "user",
            "content": (
                "Devuelve un JSON con: answer, needs_clarification, clarification_question, "
                "should_offer_incident, used_chunk_ids.\n"
                f"Estado conversacional: {json.dumps(conversation_state, ensure_ascii=False)}\n"
                f"Politica de evidencia del turno: {json.dumps(evidence_policy, ensure_ascii=False)}\n"
                f"Plan del turno: {json.dumps(planner_output, ensure_ascii=False)}\n"
                f"Estado de datasources consultados: {json.dumps(datasource_status, ensure_ascii=False)}\n"
                "Usa evidence.mode, evidence.allowed_behavior, evidence.direct_chunk_ids y semantic_confidence como contrato operativo.\n"
                "Si allowed_behavior=say_incident_resolved, explica que el caso esta resuelto y usa incidencias resueltas directas.\n"
                "Si allowed_behavior=say_incident_unresolved, explica que el caso sigue abierto o no resuelto y usa incidencias abiertas directas.\n"
                "Si allowed_behavior=answer_with_sources, responde con los fragmentos directos indicados.\n"
                "Si la politica indica requires_direct_evidence=true, no conviertas casos similares en solucion cerrada.\n"
                "Si no puedes justificar la respuesta con evidencia directa, devuelve needs_clarification=true.\n"
                "Si el turno es un follow-up insatisfecho y hay memoria conversacional recuperada, "
                "usa esa memoria para entender a que respuesta anterior se refiere el usuario.\n"
                "Si ademas hay documentos directos en el indice de conocimiento, responde con una version mas concreta; "
                "no pidas aclaracion solo porque el mensaje actual no repita todos los detalles.\n"
                f"Memoria conversacional recuperada:\n{memory_context or '[sin memoria consultada o sin resultados]'}\n"
                f"Pregunta del usuario:\n{wrap_untrusted(question, nonce=nonce, kind='pregunta_usuario')}\n"
                f"Contexto del indice de conocimiento:\n{context or '[indice no consultado o sin resultados]'}"
            ),
        },
    ]


def build_planner_messages(*, message: str, recent_messages: list[dict], conversation_state: dict) -> list[dict[str, str]]:
    nonce = new_nonce()
    return [
        {"role": "system", "content": render_system_prompt(PLANNER_PROMPT)},
        {
            "role": "user",
            "content": (
                f"Mensaje actual:\n{wrap_untrusted(message, nonce=nonce, kind='pregunta_usuario')}\n"
                f"Estado conversacional: {json.dumps(conversation_state, ensure_ascii=False)}\n"
                "Mensajes recientes:\n"
                + "\n".join(
                    wrap_untrusted(str(item.get("content", "")), nonce=nonce, kind="mensaje_previo", role=item.get("role"))
                    for item in recent_messages
                )
            ),
        },
    ]


def parse_chat_plan(content: str | None, *, fallback_message: str) -> ChatPlan:
    raw = (content or "").strip()
    if not raw:
        raise ValueError("El proveedor LLM devolvio un plan vacio")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("El proveedor LLM devolvio JSON de plan malformado") from None
        payload = json.loads(raw[start : end + 1])

    plan = ChatPlan.model_validate(payload)
    if plan.needs_knowledge_index and not plan.knowledge_index_query.strip():
        plan.knowledge_index_query = fallback_message
    if plan.needs_conversation_memory and not plan.conversation_memory_query.strip():
        plan.conversation_memory_query = fallback_message
    return plan


def parse_assistant_decision(content: str | None) -> AssistantDecision:
    raw = (content or "").strip()
    if not raw:
        raise ValueError("El proveedor LLM devolvio una respuesta vacia")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("El proveedor LLM devolvio JSON malformado") from None
        payload = json.loads(raw[start : end + 1])

    return AssistantDecision.model_validate(payload)


def validate_embedding_dimensions(embeddings: list[list[float]], expected_dimensions: int) -> list[list[float]]:
    for index, embedding in enumerate(embeddings):
        if len(embedding) != expected_dimensions:
            raise ValueError(
                f"Embedding con dimension invalida en posicion {index}: "
                f"se esperaba {expected_dimensions} y se obtuvo {len(embedding)}"
            )
    return embeddings


CONTENT_FILTER_ANSWER = (
    "No puedo procesar esta peticion: el filtro de seguridad del proveedor la ha bloqueado. "
    "Si tienes una duda operativa concreta, indica el sistema y el proceso."
)


def is_content_filter_error(exc: Exception) -> bool:
    """True si Azure (Prompt Shields / filtros de contenido) ha bloqueado la peticion o la respuesta."""
    body = getattr(exc, "body", None)
    text = str(body if body is not None else exc).lower()
    return getattr(exc, "status_code", None) == 400 and "content_filter" in text
