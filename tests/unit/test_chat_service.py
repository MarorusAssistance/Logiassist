from __future__ import annotations

from types import SimpleNamespace

from internal_assistant.chat.service import ChatService
from internal_assistant.llm.base import LLMProvider
from internal_assistant.llm.mock_provider import MockLLMProvider
from internal_assistant.repositories.memory import RetrievedMemory
from internal_assistant.rag import DEFAULT_RETRIEVAL_CONFIG, RetrievalFilters
from internal_assistant.rag.retrieval import RetrievedChunk
from internal_assistant.schemas.chat import AssistantDecision
from internal_assistant.schemas.chat import ChatPlan
from internal_assistant.schemas.chat import ChatRequest
from tests.conftest import (
    DummySession,
    FakeConversationMemoryRepository,
    FakeConversationRepository,
    FakeFeedbackRepository,
    FakeIncidentRepository,
    FakeMessageRepository,
    FakeRetriever,
    FakeRetrievalLogsRepository,
)


class StaticDecisionProvider(LLMProvider):
    def __init__(self, decision: AssistantDecision, plan: ChatPlan | None = None):
        self.decision = decision
        self.plan = plan
        self.calls = 0
        self.plan_calls = 0
        self.last_question = None
        self.last_context_chunks = None
        self.last_conversation_state = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 512 for _ in texts]

    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        self.calls += 1
        self.last_question = question
        self.last_context_chunks = context_chunks
        self.last_conversation_state = conversation_state
        return self.decision

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        self.plan_calls += 1
        return self.plan or super().plan_chat(
            message=message,
            recent_messages=recent_messages,
            conversation_state=conversation_state,
        )


def retrieved_chunk(
    *,
    chunk_id: int,
    source_type: str,
    source_id: int,
    content: str,
    system: str,
    title: str = "Fuente",
    final_score: float = 0.9,
):
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        metadata={"title": title, "affected_system": system},
        final_score=final_score,
    )


def retrieved_memory(memory_id=1, message_id=10, role="user", text="Usuario nuevo en operaciones pregunta por onboarding."):
    return RetrievedMemory(
        memory_id=memory_id,
        conversation_id=1,
        message_id=message_id,
        role=role,
        memory_text=text,
        summary=text,
        metadata={"kind": "user_message"},
        score=0.95,
    )


def build_service(results=None, llm_provider=None):
    service = ChatService(DummySession(), llm_provider=llm_provider or MockLLMProvider())
    service.conversations = FakeConversationRepository()
    service.messages = FakeMessageRepository()
    service.feedback = FakeFeedbackRepository()
    service.retrieval_logs = FakeRetrievalLogsRepository()
    service.memories = FakeConversationMemoryRepository()
    service.incidents = FakeIncidentRepository()
    service.retriever = FakeRetriever(results or [])
    return service


def test_visible_sources_are_deduplicated_by_source_and_keep_best_chunk():
    service = build_service()
    sources = service._build_visible_sources(
        [
            retrieved_chunk(
                chunk_id=10,
                source_type="incident",
                source_id=4,
                content="Primer chunk menos relevante",
                system="LogiCore ERP",
                title="Pedido retenido",
                final_score=0.40,
            ),
            retrieved_chunk(
                chunk_id=11,
                source_type="incident",
                source_id=4,
                content="Segundo chunk mas relevante",
                system="LogiCore ERP",
                title="Pedido retenido",
                final_score=0.90,
            ),
            retrieved_chunk(
                chunk_id=12,
                source_type="document",
                source_id=1,
                content="Documento relevante",
                system="LogiCore ERP",
                title="Registro de entregas parciales",
                final_score=0.80,
            ),
        ]
    )

    assert [f"{source.source_type}:{source.source_id}" for source in sources] == ["incident:4", "document:1"]
    assert sources[0].chunk_id == 11


class CapturingRetriever:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []
        self.filters = []

    def search(self, query, query_embedding, limit=5, config=None, filters=None):
        self.queries.append(query)
        self.filters.append(filters)
        return self.results[:limit]


def test_low_confidence_flow_asks_for_clarification_then_offers_incident(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.58, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])

    first = service.handle_chat(ChatRequest(user_id="u1", message="No puedo entrar"))
    second = service.handle_chat(ChatRequest(conversation_id=first.conversation_id, user_id="u1", message="Sigue igual"))
    third = service.handle_chat(ChatRequest(conversation_id=first.conversation_id, user_id="u1", message="No se"))

    assert first.needs_clarification is True
    assert second.needs_clarification is True
    assert third.should_offer_incident is True


def test_planner_clarification_flow_offers_incident_after_third_attempt(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(answer="No debe llamarse al generador final."),
        plan=ChatPlan(
            should_ask_clarification_first=True,
            needs_knowledge_index=False,
            reason="Falta contexto operativo",
        ),
    )
    service = build_service([], llm_provider=provider)

    first = service.handle_chat(ChatRequest(user_id="u1", message="Sigue fallando"))
    second = service.handle_chat(ChatRequest(conversation_id=first.conversation_id, user_id="u1", message="No tengo mas detalle"))
    third = service.handle_chat(ChatRequest(conversation_id=first.conversation_id, user_id="u1", message="Necesito dejar constancia"))

    assert first.needs_clarification is True
    assert second.needs_clarification is True
    assert third.needs_clarification is False
    assert third.should_offer_incident is True
    assert "registrar una incidencia" in third.answer
    assert provider.calls == 0


def test_policy_question_overrides_planner_clarification_and_searches_index(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="No se deben resolver cambios maestros con atajos no documentados.",
            needs_clarification=False,
            used_chunk_ids=[31],
        ),
        plan=ChatPlan(
            should_ask_clarification_first=True,
            needs_knowledge_index=False,
            reason="Falta sistema concreto",
        ),
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=31,
                source_type="document",
                source_id=20,
                content="Los cambios maestros no deben resolverse con atajos manuales no documentados.",
                system="LogiCore ERP",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(
        ChatRequest(user_id="u1", message="Se pueden resolver cambios maestros urgentes con atajos manuales no documentados")
    )

    assert response.needs_clarification is False
    assert response.sources
    assert "atajos" in response.answer
    assert provider.calls == 1


def test_chat_response_with_sources_uses_retrieved_chunks(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    results = [
        RetrievedChunk(
            chunk_id=1,
            source_type="document",
            source_id=100,
            content="Para solicitar acceso temporal en SafeGate se requiere aprobacion de Seguridad.",
            metadata={"title": "Acceso temporal", "source_url": "https://example"},
            final_score=0.9,
        )
    ]
    service = build_service(results)

    response = service.handle_chat(ChatRequest(user_id="u1", message="Como solicito acceso temporal?"))

    assert response.sources
    assert response.answer.startswith("Segun la evidencia recuperada")
    assert "Siguiente paso" not in response.answer
    assert "Fuentes consultadas" in response.fallback_text
    assert service.retrieval_logs.items[-1]["was_answered"] is True


def test_chat_stream_callback_receives_answer_tokens(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="Respuesta natural en streaming.",
            needs_clarification=False,
            used_chunk_ids=[1],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="document",
                source_id=100,
                content="Procedimiento SafeGate.",
                system="SafeGate",
            )
        ],
        llm_provider=provider,
    )
    tokens = []

    response = service.handle_chat(
        ChatRequest(user_id="u1", message="Como solicito acceso temporal en SafeGate?"),
        stream_token_callback=tokens.append,
    )

    assert response.answer == "Respuesta natural en streaming."
    assert "".join(tokens) == "Respuesta natural en streaming."


def test_chat_stream_callback_not_called_for_pre_gate_clarification(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.58, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])
    tokens = []

    response = service.handle_chat(
        ChatRequest(user_id="u1", message="No puedo entrar"),
        stream_token_callback=tokens.append,
    )

    assert response.needs_clarification is True
    assert tokens == []


def test_llm_clarification_decision_returns_question_not_solution(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    decision = AssistantDecision(
        answer="Texto que parece una solucion pero no debe usarse",
        needs_clarification=True,
        clarification_question="Que error exacto ves en AlmaTrack WMS?",
        should_offer_incident=False,
        used_chunk_ids=[],
    )
    results = [
        RetrievedChunk(
            chunk_id=1,
            source_type="incident",
            source_id=8,
            content="Caso similar de AlmaTrack WMS.",
            metadata={"title": "Caso AlmaTrack", "affected_system": "AlmaTrack WMS"},
            final_score=0.9,
        )
    ]
    service = build_service(results, llm_provider=StaticDecisionProvider(decision))

    response = service.handle_chat(ChatRequest(user_id="u1", message="Tengo una duda sobre AlmaTrack WMS."))

    assert response.needs_clarification is True
    assert response.should_offer_incident is False
    assert response.sources == []
    assert "Que error exacto ves en AlmaTrack WMS?" in response.answer
    assert "Texto que parece una solucion" not in response.answer
    assert service.retrieval_logs.items[-1]["was_answered"] is False
    assert service.retrieval_logs.items[-1]["retrieved_chunk_ids"] == [1]
    assert service.conversations.conversations[response.conversation_id].state["clarification_attempts"] == 1


def test_llm_clarification_after_max_attempts_offers_incident(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    decision = AssistantDecision(
        answer="Texto que parece una solucion pero no debe usarse",
        needs_clarification=True,
        clarification_question="Que error exacto ves en AlmaTrack WMS?",
        should_offer_incident=False,
        used_chunk_ids=[],
    )
    results = [
        RetrievedChunk(
            chunk_id=1,
            source_type="incident",
            source_id=8,
            content="Caso similar de AlmaTrack WMS.",
            metadata={"title": "Caso AlmaTrack"},
            final_score=0.9,
        )
    ]
    service = build_service(results, llm_provider=StaticDecisionProvider(decision))
    conversation = service.conversations.get_or_create(55, user_id="u1", channel_id="local")
    conversation.state = {"clarification_attempts": 2}

    response = service.handle_chat(ChatRequest(conversation_id=55, user_id="u1", message="Sigo sin tener mas detalle"))

    assert response.needs_clarification is False
    assert response.should_offer_incident is True
    assert response.sources == []
    assert "registrar una incidencia no resuelta" in response.answer
    assert "Texto que parece una solucion" not in response.answer
    assert service.retrieval_logs.items[-1]["was_answered"] is False


def test_new_issue_with_only_similar_incidents_is_policy_clarification(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    decision = AssistantDecision(
        answer="La solucion es forzar una resincronizacion.",
        needs_clarification=False,
        clarification_question=None,
        should_offer_incident=False,
        used_chunk_ids=[1],
    )
    provider = StaticDecisionProvider(decision)
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="incident",
                source_id=38,
                content="Incidencia resuelta similar de AlmaTrack WMS.",
                system="AlmaTrack WMS",
                title="Sincronizacion incompleta",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(ChatRequest(user_id="u1", message="No encuentro solucion para un error nuevo en AlmaTrack WMS."))

    assert response.needs_clarification is True
    assert response.sources == []
    assert "error exacto" in response.answer
    assert "resincronizacion" not in response.answer
    assert provider.calls == 0
    assert service.retrieval_logs.items[-1]["was_answered"] is False
    assert service.retrieval_logs.items[-1]["retrieved_chunk_ids"] == [1]


def test_procedure_request_with_relevant_document_stays_normal(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    decision = AssistantDecision(
        answer="Marca los bultos expedidos y los retenidos por separado.",
        needs_clarification=False,
        clarification_question=None,
        should_offer_incident=False,
        used_chunk_ids=[1],
    )
    provider = StaticDecisionProvider(decision)
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="document",
                source_id=1,
                content="Procedimiento de entrega parcial en LogiCore ERP.",
                system="LogiCore ERP",
                title="Registro de entregas parciales",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(ChatRequest(user_id="u1", message="Como registro una entrega parcial en LogiCore ERP?"))

    assert response.needs_clarification is False
    assert response.sources
    assert "Marca los bultos" in response.answer
    assert provider.calls == 1
    assert service.retrieval_logs.items[-1]["was_answered"] is True


def test_procedure_request_without_document_is_policy_clarification(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="Usa el caso resuelto como procedimiento.",
            needs_clarification=False,
            clarification_question=None,
            should_offer_incident=False,
            used_chunk_ids=[1],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="incident",
                source_id=22,
                content="Incidencia parecida de LogiCore ERP.",
                system="LogiCore ERP",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(ChatRequest(user_id="u1", message="Que pasos debo seguir para una entrega parcial en LogiCore ERP?"))

    assert response.needs_clarification is True
    assert response.sources == []
    assert "procedimiento documentado" in response.answer
    assert provider.calls == 0


def test_system_mismatch_is_policy_clarification(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="Respuesta usando SafeGate.",
            needs_clarification=False,
            clarification_question=None,
            should_offer_incident=False,
            used_chunk_ids=[1],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="document",
                source_id=9,
                content="Procedimiento de SafeGate.",
                system="SafeGate",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(ChatRequest(user_id="u1", message="No encuentro solucion para un error nuevo en AlmaTrack WMS."))

    assert response.needs_clarification is True
    assert response.sources == []
    assert "no pertenecen al sistema" in response.answer
    assert provider.calls == 0


def test_resolved_incident_request_answers_with_direct_incident(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="El caso se resolvio reemitiendo la credencial temporal y validando el acceso.",
            needs_clarification=False,
            used_chunk_ids=[21],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=21,
                source_type="incident",
                source_id=44,
                content="Incidencia resuelta de visita tecnica en SafeGate.",
                system="SafeGate",
            )
        ],
        llm_provider=provider,
    )
    service.incidents.items = [
        SimpleNamespace(id=44, external_id="INC-044", title="Visita tecnica rechazada", status="resolved", is_resolved=True)
    ]

    response = service.handle_chat(ChatRequest(user_id="u1", message="Tengo una visita tecnica con acceso temporal rechazado en SafeGate. Como se resolvio el caso conocido"))

    assert response.needs_clarification is False
    assert response.sources
    assert "reemit" in response.answer
    assert provider.calls == 1
    assert service.retrieval_logs.items[-1]["was_answered"] is True


def test_unresolved_incident_request_answers_with_open_incident(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="El caso parecido sigue abierto y no tiene resolucion documentada.",
            needs_clarification=False,
            used_chunk_ids=[22],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=22,
                source_type="incident",
                source_id=56,
                content="Incidencia abierta de ubicacion RF inconsistente en AlmaTrack WMS.",
                system="AlmaTrack WMS",
            )
        ],
        llm_provider=provider,
    )
    service.incidents.items = [
        SimpleNamespace(id=56, external_id="INC-056", title="Ubicacion RF inconsistente", status="open", is_resolved=False)
    ]

    response = service.handle_chat(ChatRequest(user_id="u1", message="Tengo una ubicacion RF inconsistente despues de una reposicion en AlmaTrack WMS. Hay un caso abierto parecido"))

    assert response.needs_clarification is False
    assert response.sources
    assert "sigue abierto" in response.answer
    assert provider.calls == 1


def test_status_request_answers_when_single_incident_status_is_available(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="La ruta congelada sigue abierta y requiere revision operativa.",
            needs_clarification=False,
            used_chunk_ids=[23],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=23,
                source_type="incident",
                source_id=55,
                content="Incidencia abierta de ruta congelada en RutaNexo.",
                system="RutaNexo",
            )
        ],
        llm_provider=provider,
    )
    service.incidents.items = [
        SimpleNamespace(id=55, external_id="INC-055", title="Ruta congelada", status="open", is_resolved=False)
    ]

    response = service.handle_chat(ChatRequest(user_id="u1", message="En RutaNexo una ruta queda congelada tras cambiar una parada prioritaria. Esta resuelta o sigue abierta"))

    assert response.needs_clarification is False
    assert response.sources
    assert "sigue abierta" in response.answer
    assert provider.calls == 1


def test_post_gate_overrides_llm_answer_when_policy_is_restrictive(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    monkeypatch.setattr(ChatService, "_should_pre_gate_answer", lambda self, assessment: False)
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="La solucion cerrada no debe pasar.",
            needs_clarification=False,
            clarification_question=None,
            should_offer_incident=False,
            used_chunk_ids=[1],
        )
    )
    service = build_service(
        [
            retrieved_chunk(
                chunk_id=1,
                source_type="incident",
                source_id=38,
                content="Incidencia resuelta similar de AlmaTrack WMS.",
                system="AlmaTrack WMS",
            )
        ],
        llm_provider=provider,
    )

    response = service.handle_chat(ChatRequest(user_id="u1", message="No encuentro solucion para un error nuevo en AlmaTrack WMS."))

    assert response.needs_clarification is True
    assert response.sources == []
    assert "solucion cerrada" not in response.answer
    assert provider.calls == 1
    assert service.retrieval_logs.items[-1]["was_answered"] is False


def test_planner_followup_uses_memory_and_abstract_index_query(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan(
        intent="question_answering",
        needs_conversation_memory=True,
        needs_knowledge_index=True,
        can_answer_from_conversation_only=False,
        should_ask_clarification_first=False,
        conversation_memory_query="respuesta anterior sobre onboarding para nuevo empleado en operaciones",
        knowledge_index_query="procedimiento de onboarding e iniciacion para nuevo empleado del equipo de operaciones",
        user_context_summary="Usuario nuevo en operaciones.",
        expected_source_preference=["document"],
        mentioned_systems=[],
        retrieval_filters=RetrievalFilters(source_types=["document"], departments=["Onboarding"]),
        filter_reason="follow-up onboarding",
        reason="follow_up",
    )
    provider = StaticDecisionProvider(
        AssistantDecision(
            answer="Completa manuales, alta en OnboardHub y permisos iniciales.",
            needs_clarification=False,
            should_offer_incident=False,
            used_chunk_ids=[7],
        ),
        plan=plan,
    )
    result = retrieved_chunk(
        chunk_id=7,
        source_type="document",
        source_id=4,
        content="Checklist de onboarding para operaciones.",
        system="OnboardHub",
    )
    service = build_service([result], llm_provider=provider)
    service.retriever = CapturingRetriever([result])
    service.memories = FakeConversationMemoryRepository([retrieved_memory()])
    service.conversations.get_or_create(20, user_id="u1", channel_id="local")
    service.messages.create(20, "user", "Soy nuevo en operaciones. Que pasos de onboarding debo completar?", intent="question_answering")

    response = service.handle_chat(ChatRequest(conversation_id=20, user_id="u1", message="esa respuesta no me vale, indicame mejor los pasos"))

    assert response.needs_clarification is False
    assert service.retriever.queries == [plan.knowledge_index_query]
    assert service.retriever.filters[0].source_types == ["document"]
    assert service.retriever.filters[0].departments == ["Onboarding"]
    assert service.memories.search_calls
    assert provider.last_conversation_state["_conversation_memory"]
    assert provider.last_conversation_state["_planner_output"]["knowledge_index_query"] == plan.knowledge_index_query
    assert provider.last_conversation_state["_datasource_status"]["knowledge_index"]["retrieval_filters"]["source_types"] == ["document"]


def test_planner_memory_only_skips_chunk_retrieval(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan(
        intent="question_answering",
        needs_conversation_memory=True,
        needs_knowledge_index=False,
        can_answer_from_conversation_only=True,
        should_ask_clarification_first=False,
        conversation_memory_query="que dijo el usuario sobre operaciones",
        knowledge_index_query="",
        user_context_summary="El usuario pregunta por la conversacion.",
        expected_source_preference=[],
        mentioned_systems=[],
        reason="conversation_only",
    )
    provider = StaticDecisionProvider(
        AssistantDecision(answer="Dijiste que eras nuevo en operaciones.", needs_clarification=False, used_chunk_ids=[]),
        plan=plan,
    )
    service = build_service([], llm_provider=provider)
    service.retriever = CapturingRetriever([])
    service.memories = FakeConversationMemoryRepository([retrieved_memory(text="Usuario dijo que era nuevo en operaciones.")])

    response = service.handle_chat(ChatRequest(user_id="u1", message="Que te dije antes sobre mi equipo?"))

    assert response.needs_clarification is False
    assert service.retriever.queries == []
    assert service.memories.search_calls
    assert provider.last_context_chunks == []
    assert provider.last_conversation_state["_conversation_memory"]


def test_planner_index_only_skips_memory_search(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan.fallback("Como registro una entrega parcial en LogiCore ERP?")
    provider = StaticDecisionProvider(
        AssistantDecision(answer="Marca bultos expedidos y retenidos.", needs_clarification=False, used_chunk_ids=[1]),
        plan=plan,
    )
    result = retrieved_chunk(
        chunk_id=1,
        source_type="document",
        source_id=1,
        content="Procedimiento de entrega parcial.",
        system="LogiCore ERP",
    )
    service = build_service([result], llm_provider=provider)
    service.retriever = CapturingRetriever([result])
    service.memories = FakeConversationMemoryRepository([retrieved_memory()])

    response = service.handle_chat(ChatRequest(user_id="u1", message="Como registro una entrega parcial en LogiCore ERP?"))

    assert response.needs_clarification is False
    assert service.retriever.queries == [plan.knowledge_index_query]
    assert service.memories.search_calls == []
    assert "_conversation_memory" not in provider.last_conversation_state


def test_planner_filters_are_passed_to_retriever_for_resolved_case(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan(
        needs_knowledge_index=True,
        knowledge_index_query="visita tecnica SafeGate acceso temporal rechazado",
        expected_source_preference=["incident"],
        retrieval_filters=RetrievalFilters(source_types=["incident"], incident_statuses=["resolved"], is_resolved=True),
        filter_reason="caso resuelto",
    )
    provider = StaticDecisionProvider(
        AssistantDecision(answer="La incidencia fue resuelta con validacion manual.", needs_clarification=False, used_chunk_ids=[44]),
        plan=plan,
    )
    result = retrieved_chunk(
        chunk_id=44,
        source_type="incident",
        source_id=44,
        content="Visita tecnica con acceso temporal rechazado en SafeGate resuelta.",
        system="SafeGate",
    )
    service = build_service([result], llm_provider=provider)
    service.retriever = CapturingRetriever([result])
    service.incidents.items = [SimpleNamespace(id=44, external_id="INC-044", title="Visita tecnica", status="resolved", is_resolved=True)]

    response = service.handle_chat(ChatRequest(user_id="u1", message="Como se resolvio la visita tecnica de SafeGate?"))

    assert response.needs_clarification is False
    assert service.retriever.filters[0].source_types == ["incident"]
    assert service.retriever.filters[0].incident_statuses == ["resolved"]
    assert service.retriever.filters[0].is_resolved is True


def test_requested_empty_index_is_signaled_when_memory_exists(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan(
        intent="question_answering",
        needs_conversation_memory=True,
        needs_knowledge_index=True,
        can_answer_from_conversation_only=False,
        should_ask_clarification_first=False,
        conversation_memory_query="contexto anterior",
        knowledge_index_query="procedimiento inexistente",
        user_context_summary="",
        expected_source_preference=["document"],
        mentioned_systems=[],
        reason="needs_both",
    )
    provider = StaticDecisionProvider(
        AssistantDecision(answer="Con la memoria recuperada puedo responder parcialmente.", needs_clarification=False, used_chunk_ids=[]),
        plan=plan,
    )
    service = build_service([], llm_provider=provider)
    service.retriever = CapturingRetriever([])
    service.memories = FakeConversationMemoryRepository([retrieved_memory()])

    response = service.handle_chat(ChatRequest(user_id="u1", message="mejor teniendo en cuenta lo anterior"))

    assert response.needs_clarification is False
    assert provider.last_conversation_state["_datasource_status"]["knowledge_index"]["requested"] is True
    assert provider.last_conversation_state["_datasource_status"]["knowledge_index"]["result_count"] == 0


def test_planner_hallucinated_system_does_not_force_mismatch_when_user_did_not_mention_it(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    plan = ChatPlan(
        intent="question_answering",
        needs_conversation_memory=False,
        needs_knowledge_index=True,
        can_answer_from_conversation_only=False,
        should_ask_clarification_first=False,
        conversation_memory_query="",
        knowledge_index_query="procedimiento de onboarding para operadores en LogiCore ERP",
        user_context_summary="Usuario nuevo en operaciones.",
        expected_source_preference=["document"],
        mentioned_systems=["LogiCore ERP"],
        reason="planner_over_specific",
    )
    provider = StaticDecisionProvider(
        AssistantDecision(answer="Completa el checklist de bienvenida.", needs_clarification=False, used_chunk_ids=[10]),
        plan=plan,
    )
    result = retrieved_chunk(
        chunk_id=10,
        source_type="document",
        source_id=4,
        content="Checklist de onboarding en OnboardHub.",
        system="OnboardHub",
    )
    service = build_service([result], llm_provider=provider)
    service.retriever = CapturingRetriever([result])

    response = service.handle_chat(ChatRequest(user_id="u1", message="Soy nuevo en operaciones. Que pasos de onboarding debo completar?"))

    assert response.needs_clarification is False
    assert response.sources
    assert "Completa el checklist" in response.answer


def test_planner_clarification_does_not_expose_internal_reason(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])
    plan = ChatPlan(
        intent="question_answering",
        needs_conversation_memory=False,
        needs_knowledge_index=False,
        can_answer_from_conversation_only=False,
        should_ask_clarification_first=True,
        conversation_memory_query="",
        knowledge_index_query="",
        user_context_summary="",
        expected_source_preference=[],
        mentioned_systems=["AlmaTrack WMS"],
        reason="El sistema no esta configurado para el usuario por la conversacion anterior.",
    )

    answer = service._build_planner_clarification_answer(plan, 1)

    assert "no esta configurado" not in answer
    assert "AlmaTrack WMS" in answer
    assert "error exacto" in answer


def test_memory_indexing_failure_does_not_break_chat(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    result = retrieved_chunk(
        chunk_id=1,
        source_type="document",
        source_id=1,
        content="Procedimiento de entrega parcial.",
        system="LogiCore ERP",
    )
    service = build_service([result])

    class FailingMemoryRepository(FakeConversationMemoryRepository):
        def upsert_for_message(self, **kwargs):
            raise RuntimeError("memory db down")

    service.memories = FailingMemoryRepository()

    response = service.handle_chat(ChatRequest(user_id="u1", message="Como se controla un pedido intercentro?"))

    assert response.answer
    assert response.conversation_id == 1


def test_register_unresolved_incident_flow(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.58, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])
    created = {"id": 321, "external_id": "INC-00321", "title": "Error SafeGate", "affected_system": "SafeGate", "status": "open"}
    monkeypatch.setattr(service, "_create_incident", lambda payload: created)
    monkeypatch.setattr(service, "_trigger_index_incident", lambda incident_id: None)

    third = None
    for message in ["No puedo entrar", "Sigue fallando", "No tengo mas detalle"]:
        third = service.handle_chat(ChatRequest(conversation_id=third.conversation_id if third else None, user_id="u1", message=message))

    start = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="si"))
    fill_title = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="titulo: Error SafeGate"))
    fill_description = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="descripcion: No permite acceder al torno"))
    fill_department = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="departamento: Seguridad"))
    fill_category = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="categoria: accesos"))
    fill_system = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="sistema: SafeGate"))
    fill_impact = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="impacto: bloquea el acceso de guardia"))
    fill_expected = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="esperado: deberia abrir la puerta"))
    confirm_summary = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="actual: indica acceso denegado"))
    created_response = service.handle_chat(ChatRequest(conversation_id=third.conversation_id, user_id="u1", message="si"))

    assert start.answer.startswith("Necesito el titulo")
    assert "Resumen de la incidencia" in confirm_summary.answer
    assert created_response.created_ticket_external_id == "INC-00321"
    assert created_response.created_ticket_id == 321
    assert "Incidencia registrada" in created_response.answer


def test_feedback_useful_and_not_useful_messages_are_stored(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.58, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])

    useful = service.handle_chat(ChatRequest(user_id="u1", message="util gracias"))
    not_useful = service.handle_chat(ChatRequest(conversation_id=useful.conversation_id, user_id="u1", message="no util no me ayudo"))

    assert useful.answer.startswith("Gracias")
    assert len(service.feedback.items) == 2


def test_handle_chat_uses_default_retrieval_config(monkeypatch):
    monkeypatch.setattr("internal_assistant.chat.service.get_settings", lambda: SimpleNamespace(retrieval_confidence_threshold=0.10, app_shared_secret="secret", custom_incidents_api_base_url="http://test", indexer_api_base_url="http://test"))
    service = build_service([])

    class CapturingRetriever:
        def __init__(self):
            self.config = None

        def search(self, query, query_embedding, limit=5, config=None, filters=None):
            self.config = config
            return [
                RetrievedChunk(
                    chunk_id=1,
                    source_type="document",
                    source_id=100,
                    content="LogiCore ERP requiere trazabilidad operativa.",
                    metadata={"title": "Control operativo", "source_url": "https://example"},
                    final_score=0.9,
                )
            ]

    retriever = CapturingRetriever()
    service.retriever = retriever

    service.handle_chat(ChatRequest(user_id="u1", message="Como se controla un pedido intercentro?"))

    assert retriever.config is not None
    assert retriever.config.top_k == DEFAULT_RETRIEVAL_CONFIG.top_k
    assert retriever.config.vector_weight == DEFAULT_RETRIEVAL_CONFIG.vector_weight
    assert retriever.config.text_weight == DEFAULT_RETRIEVAL_CONFIG.text_weight
