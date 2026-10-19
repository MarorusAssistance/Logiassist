from __future__ import annotations

import pytest

from internal_assistant.chat.memory import build_message_memory_text, summarize_message
from internal_assistant.llm.common import parse_chat_plan
from internal_assistant.llm.mock_provider import MockLLMProvider
from internal_assistant.schemas.chat import ChatPlan


def test_mock_planner_followup_uses_memory_and_index():
    provider = MockLLMProvider()

    plan = provider.plan_chat(
        message="esa respuesta no me vale, explicamelo mejor",
        recent_messages=[{"role": "user", "content": "Soy nuevo en operaciones. Que pasos de onboarding debo completar?"}],
        conversation_state={},
    )

    assert plan.needs_conversation_memory is True
    assert plan.needs_knowledge_index is True
    assert "onboarding" in plan.knowledge_index_query
    assert "document" in plan.retrieval_filters.source_types


def test_mock_planner_new_question_uses_knowledge_index_only():
    provider = MockLLMProvider()

    plan = provider.plan_chat(
        message="Como registro una entrega parcial en LogiCore ERP?",
        recent_messages=[],
        conversation_state={},
    )

    assert plan.needs_conversation_memory is False
    assert plan.needs_knowledge_index is True
    assert plan.knowledge_index_query == "Como registro una entrega parcial en LogiCore ERP?"
    assert plan.retrieval_filters.source_types == ["document"]
    assert plan.retrieval_filters.document_types == ["procedimiento"]


def test_mock_planner_conversation_only_request():
    provider = MockLLMProvider()

    plan = provider.plan_chat(
        message="Que te dije antes sobre mi equipo?",
        recent_messages=[{"role": "user", "content": "Soy nuevo en operaciones"}],
        conversation_state={},
    )

    assert plan.can_answer_from_conversation_only is True
    assert plan.needs_conversation_memory is True
    assert plan.needs_knowledge_index is False


def test_mock_planner_resolved_case_prefers_resolved_incidents():
    provider = MockLLMProvider()

    plan = provider.plan_chat(
        message="Como se resolvio la visita tecnica con acceso temporal rechazado en SafeGate?",
        recent_messages=[],
        conversation_state={},
    )

    assert plan.retrieval_filters.source_types == ["incident"]
    assert plan.retrieval_filters.incident_statuses == ["resolved"]
    assert plan.retrieval_filters.is_resolved is True


def test_parse_invalid_plan_raises_for_service_fallback():
    with pytest.raises(ValueError):
        parse_chat_plan("no es json", fallback_message="pregunta")


def test_chat_plan_fallback_uses_original_message_as_index_query():
    plan = ChatPlan.fallback("pregunta original")

    assert plan.needs_knowledge_index is True
    assert plan.needs_conversation_memory is False
    assert plan.knowledge_index_query == "pregunta original"


def test_chat_plan_normalizes_query_lists_from_llm():
    plan = ChatPlan(
        knowledge_index_query=["procedimiento", "validacion manual"],
        conversation_memory_query=["contexto", "anterior"],
    )

    assert plan.knowledge_index_query == "procedimiento validacion manual"
    assert plan.conversation_memory_query == "contexto anterior"


def test_chat_plan_normalizes_and_discards_invalid_retrieval_filters():
    plan = ChatPlan(
        retrieval_filters={
            "source_types": ["document", "bad"],
            "affected_systems": ["SafeGate", "NoExiste"],
            "departments": ["Seguridad", "Ventas"],
            "document_types": ["guia", "manual"],
            "incident_statuses": ["resolved", "pending"],
            "is_resolved": True,
        },
        filter_reason=["filtro", "evidente"],
    )

    assert plan.retrieval_filters.source_types == ["document"]
    assert plan.retrieval_filters.affected_systems == ["SafeGate"]
    assert plan.retrieval_filters.departments == ["Seguridad"]
    assert plan.retrieval_filters.document_types == ["guía"]
    assert plan.retrieval_filters.incident_statuses == ["resolved"]
    assert plan.retrieval_filters.is_resolved is True
    assert plan.filter_reason == "filtro evidente"


def test_chat_plan_recovers_filters_misplaced_in_expected_source_preference():
    plan = ChatPlan(
        expected_source_preference={
            "source_types": ["incident"],
            "incident_statuses": ["resolved"],
            "is_resolved": True,
        }
    )

    assert plan.expected_source_preference == ["incident"]
    assert plan.retrieval_filters.source_types == ["incident"]
    assert plan.retrieval_filters.incident_statuses == ["resolved"]
    assert plan.retrieval_filters.is_resolved is True


def test_memory_text_includes_raw_summary_and_metadata():
    summary = summarize_message("Soy nuevo en operaciones y necesito onboarding.")
    memory_text = build_message_memory_text(
        role="user",
        content="Soy nuevo en operaciones y necesito onboarding.",
        summary=summary,
        metadata={"mentioned_systems": ["OnboardHub"], "source_keys": ["document:4"]},
    )

    assert "role: user" in memory_text
    assert "summary: Soy nuevo" in memory_text
    assert "systems: OnboardHub" in memory_text
    assert "sources: document:4" in memory_text
    assert "content: Soy nuevo" in memory_text
