from __future__ import annotations

import time
import re
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.orm import Session

from internal_assistant.cards import build_feedback_card, build_incident_confirmation_card, build_sources_card
from internal_assistant.chat.evidence import assess_evidence, build_policy_clarification_answer, detect_query_signals
from internal_assistant.chat.incident_draft import build_confirmation_text, extract_incident_fields, missing_fields, validate_draft
from internal_assistant.chat.intents import detect_intent
from internal_assistant.chat.memory import build_message_memory_text, summarize_message
from internal_assistant.config import get_settings
from internal_assistant.llm import LLMProvider, build_default_provider
from internal_assistant.llm.common import build_chat_messages
from internal_assistant.observability import RagTrace, get_logger, start_span
from internal_assistant.observability.tracing import (
    retrieval_span_attributes,
    serialize_chunks_for_langsmith,
    serialize_context_for_langsmith,
    serialize_decision_for_langsmith,
    set_span_attributes,
    user_hash,
)
from internal_assistant.repositories import (
    ConversationMemoryRepository,
    ConversationRepository,
    FeedbackRepository,
    IncidentRepository,
    MessageRepository,
    RetrievalLogRepository,
)
from internal_assistant.rag.keywords import build_keyword_query
from internal_assistant.rag import DEFAULT_RETRIEVAL_CONFIG, HybridRetriever, RetrievalConfig, RetrievalFilters
from internal_assistant.schemas import ChatPlan, ChatRequest, ChatResponse, FeedbackCreate, SourceSnippet
from internal_assistant.security.prompt_guard import (
    GuardResult,
    StreamingOutputGuard,
    build_refusal_answer,
    check_output,
    scan_retrieved_text,
    scan_user_input,
)

logger = get_logger(__name__)


class ChatService:
    def __init__(self, session: Session, llm_provider: LLMProvider | None = None) -> None:
        self.session = session
        self.settings = get_settings()
        self.llm_provider = llm_provider or build_default_provider()
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.incidents = IncidentRepository(session)
        self.feedback = FeedbackRepository(session)
        self.retrieval_logs = RetrievalLogRepository(session)
        self.memories = ConversationMemoryRepository(session)
        self.retriever = HybridRetriever(session)
        self.last_output_findings: list[str] = []
        self.last_retrieval_debug: dict[str, Any] = {}
        self.last_output_blocked = False

    def classify_intent(self, message: str, state: dict | None = None) -> str:
        return detect_intent(message, state)

    def retrieve(
        self,
        message: str,
        retrieval_config: RetrievalConfig | None = None,
        retrieval_filters: RetrievalFilters | dict | None = None,
        keyword_query: str | None = None,
    ) -> list:
        query_embedding = self.llm_provider.embed_texts([message])[0]
        return self.retriever.search(
            message,
            query_embedding,
            config=retrieval_config or DEFAULT_RETRIEVAL_CONFIG,
            filters=retrieval_filters,
            keyword_query=keyword_query,
        )

    def calculate_confidence(self, retrieved: list) -> float:
        return retrieved[0].final_score if retrieved else 0.0

    def generate_answer(
        self,
        question: str,
        retrieved_chunks: list,
        conversation_state: dict,
        *,
        evidence_policy: dict[str, Any] | None = None,
        planner_output: dict[str, Any] | None = None,
        conversation_memory: list[dict[str, Any]] | None = None,
        datasource_status: dict[str, Any] | None = None,
    ) -> tuple[Any, list[dict[str, Any]]]:
        context_chunks = [
            {
                "chunk_id": item.chunk_id,
                "content": item.content,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "metadata": item.metadata,
            }
            for item in retrieved_chunks
        ]
        llm_state = dict(conversation_state)
        if evidence_policy:
            llm_state["_evidence_policy"] = evidence_policy
        if planner_output:
            llm_state["_planner_output"] = planner_output
        if conversation_memory is not None:
            llm_state["_conversation_memory"] = conversation_memory
        if datasource_status:
            llm_state["_datasource_status"] = datasource_status
        decision = self.llm_provider.generate_chat_response(
            question=question,
            context_chunks=context_chunks,
            conversation_state=llm_state,
        )
        return self._apply_output_guard(decision, context_chunks), context_chunks

    def stream_generate_answer(
        self,
        question: str,
        retrieved_chunks: list,
        conversation_state: dict,
        *,
        on_token: Callable[[str], None],
        evidence_policy: dict[str, Any] | None = None,
        planner_output: dict[str, Any] | None = None,
        conversation_memory: list[dict[str, Any]] | None = None,
        datasource_status: dict[str, Any] | None = None,
    ) -> tuple[Any, list[dict[str, Any]]]:
        context_chunks = [
            {
                "chunk_id": item.chunk_id,
                "content": item.content,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "metadata": item.metadata,
            }
            for item in retrieved_chunks
        ]
        llm_state = dict(conversation_state)
        if evidence_policy:
            llm_state["_evidence_policy"] = evidence_policy
        if planner_output:
            llm_state["_planner_output"] = planner_output
        if conversation_memory is not None:
            llm_state["_conversation_memory"] = conversation_memory
        if datasource_status:
            llm_state["_datasource_status"] = datasource_status

        final_decision = None
        guarded_on_token = StreamingOutputGuard(on_token)
        for event in self.llm_provider.stream_chat_response(
            question=question,
            context_chunks=context_chunks,
            conversation_state=llm_state,
        ):
            if event.kind == "token" and event.text:
                guarded_on_token(event.text)
            elif event.kind == "final":
                final_decision = event.decision

        if final_decision is None:
            raise RuntimeError("El proveedor LLM no devolvio decision final en streaming")
        return self._apply_output_guard(final_decision, context_chunks), context_chunks

    def _apply_output_guard(self, decision: Any, context_chunks: list[dict[str, Any]]) -> Any:
        """Valida la salida del LLM antes de mostrarla: fugas, secretos, exfiltracion y citas inventadas."""
        allowed_chunk_ids = {int(item["chunk_id"]) for item in context_chunks}
        decision.used_chunk_ids = [chunk_id for chunk_id in decision.used_chunk_ids if chunk_id in allowed_chunk_ids]
        answer_check = check_output(decision.answer)
        clarification_check = check_output(decision.clarification_question or "")
        self.last_output_findings = answer_check.findings + clarification_check.findings
        self.last_output_blocked = answer_check.blocked or clarification_check.blocked
        if self.last_output_blocked:
            logger.warning("Salida del LLM bloqueada por el guardia de seguridad: %s", self.last_output_findings)
            blocked_answer = answer_check.answer if answer_check.blocked else clarification_check.answer
            return decision.model_copy(
                update={
                    "answer": blocked_answer,
                    "needs_clarification": False,
                    "clarification_question": None,
                    "should_offer_incident": False,
                    "used_chunk_ids": [],
                }
            )
        decision.answer = answer_check.answer
        if decision.clarification_question:
            decision.clarification_question = clarification_check.answer
        return decision

    def simulate_chat(
        self,
        request: ChatRequest,
        *,
        state: dict | None = None,
        retrieval_config: RetrievalConfig | None = None,
    ) -> tuple[ChatResponse, dict[str, Any], dict[str, Any]]:
        current_state = dict(state or {})
        intent = self.classify_intent(request.message, current_state)
        if current_state.get("pending_incident_draft"):
            raise ValueError("El flujo de evaluacion solo soporta preguntas/respuestas del asistente RAG")
        if intent in {"feedback", "register_incident", "confirm_incident"}:
            intent = "question_answer"

        trace = RagTrace.from_settings(self.settings)
        with trace.start_run(
            "rag.chat",
            inputs={"question": request.message, "channel": request.channel},
            metadata={
                "conversation_id": request.conversation_id or 0,
                "user_id_hash": user_hash(request.user_id),
                "mode": "simulation",
            },
        ) as trace_run:
            input_guard = scan_user_input(request.message)
            if input_guard.blocked:
                response = self._blocked_input_response(
                    request.conversation_id or 0,
                    current_state,
                    input_guard,
                    started_at=time.perf_counter(),
                    trace_run=trace_run,
                )
            else:
                response = self._handle_qa_flow(
                    conversation_id=request.conversation_id or 0,
                    state=current_state,
                    request=request,
                    intent=intent,
                    user_message_id=0,
                    started_at=time.perf_counter(),
                    write_retrieval_logs=False,
                    retrieval_config=retrieval_config,
                    trace_run=trace_run,
                )
            meta = response.pop("_meta", {})
            meta["retrieval_debug"] = self.last_retrieval_debug
            next_state = response.pop("_state")
            chat_response = ChatResponse(**response)
            trace_run.set_outputs(self._trace_response_payload(chat_response))
            return chat_response, next_state, meta

    def handle_chat(self, request: ChatRequest, *, stream_token_callback: Callable[[str], None] | None = None) -> ChatResponse:
        started_at = time.perf_counter()
        trace = RagTrace.from_settings(self.settings)
        with trace.start_run(
            "rag.chat",
            inputs={"question": request.message, "channel": request.channel},
            metadata={
                "conversation_id": request.conversation_id,
                "user_id_hash": user_hash(request.user_id),
                "provider": getattr(self.settings, "llm_provider", ""),
                "chat_model": getattr(self.settings, "chat_model", ""),
                "embedding_model": getattr(self.settings, "embedding_model", ""),
            },
        ) as trace_run, start_span(
            "chat.request",
            {
                "channel": request.channel,
                "conversation_id": request.conversation_id or 0,
                "user_id_hash": user_hash(request.user_id),
                "message.length": len(request.message),
                "llm.provider": getattr(self.settings, "llm_provider", ""),
                "llm.chat_model": getattr(self.settings, "chat_model", ""),
                "llm.embedding_model": getattr(self.settings, "embedding_model", ""),
            },
        ) as request_span:
            conversation = self.conversations.get_or_create(
                request.conversation_id,
                user_id=request.user_id,
                channel_id=request.channel,
                teams_conversation_id=request.teams_conversation_id,
            )
            state = dict(conversation.state or {})
            intent = self.classify_intent(request.message, state)
            set_span_attributes(
                request_span,
                {"conversation_id": conversation.id, "detected_intent": intent},
            )
            user_message = self.messages.create(conversation.id, "user", request.message, intent=intent)
            input_guard = scan_user_input(request.message)
            set_span_attributes(
                request_span,
                {"security.input_risk": input_guard.risk, "security.input_categories": input_guard.categories},
            )
            if input_guard.blocked:
                logger.warning(
                    "Mensaje bloqueado por el guardia de entrada (conversation_id=%s, categorias=%s)",
                    conversation.id,
                    input_guard.categories,
                )

            if intent == "feedback" and not input_guard.blocked:
                chat_response = self._handle_feedback_message(conversation.id, user_message.id, request)
                trace_run.set_outputs(self._trace_response_payload(chat_response))
                set_span_attributes(
                    request_span,
                    {
                        "chat.status": "ok",
                        "message_id": chat_response.message_id or 0,
                        "answer.length": len(chat_response.answer),
                    },
                )
                return chat_response

            if not input_guard.blocked and (
                intent in {"register_incident", "confirm_incident"} or state.get("pending_incident_draft")
            ):
                with trace_run.child(
                    "rag.incident_flow",
                    run_type="chain",
                    inputs={"message": request.message, "state": state},
                ) as incident_run:
                    response = self._handle_incident_flow(conversation.id, state, request)
                    incident_run.set_outputs(
                        {
                            "answer": response.get("answer"),
                            "created_ticket_id": response.get("created_ticket_id"),
                            "created_ticket_external_id": response.get("created_ticket_external_id"),
                        }
                    )
                with start_span(
                    "chat.persist",
                    {"conversation_id": conversation.id, "intent": "register_incident"},
                ):
                    self.conversations.save_state(conversation, response.pop("_state"))
                    assistant_message = self.messages.create(
                        conversation.id,
                        "assistant",
                        response["answer"],
                        intent="register_incident",
                        created_ticket_id=response.get("created_ticket_id"),
                    )
                    self.session.commit()
                response["message_id"] = assistant_message.id
                chat_response = ChatResponse(**response)
                trace_run.set_outputs(self._trace_response_payload(chat_response))
                set_span_attributes(
                    request_span,
                    {
                        "chat.status": "ok",
                        "message_id": chat_response.message_id or 0,
                        "answer.length": len(chat_response.answer),
                    },
                )
                return chat_response

            if input_guard.blocked:
                response = self._blocked_input_response(
                    conversation.id,
                    state,
                    input_guard,
                    started_at=started_at,
                    trace_run=trace_run,
                )
            else:
                response = self._handle_qa_flow(
                    conversation.id,
                    state,
                    request,
                    intent,
                    user_message.id,
                    started_at,
                    trace_run=trace_run,
                    stream_token_callback=stream_token_callback,
                )
            meta = response.get("_meta", {})
            with start_span("chat.persist", {"conversation_id": conversation.id, "intent": intent}):
                self.conversations.save_state(conversation, response.pop("_state"))
                assistant_message = self.messages.create(conversation.id, "assistant", response["answer"], intent=intent)
                self.session.commit()
            response["message_id"] = assistant_message.id
            chat_response = ChatResponse(**response)
            if not input_guard.blocked:
                # Los turnos bloqueados no entran en memoria para no reinyectarlos en turnos futuros.
                self._index_turn_memory(
                    conversation_id=conversation.id,
                    user_message=user_message,
                    assistant_message=assistant_message,
                    chat_response=chat_response,
                    meta=meta,
                )
            trace_run.set_outputs(self._trace_response_payload(chat_response))
            set_span_attributes(
                request_span,
                {
                    "chat.status": "ok",
                    "message_id": chat_response.message_id or 0,
                    "answer.length": len(chat_response.answer),
                    "source.count": len(chat_response.sources),
                    "needs_clarification": chat_response.needs_clarification,
                    "should_offer_incident": chat_response.should_offer_incident,
                },
            )
            return chat_response

    def save_feedback(self, payload: FeedbackCreate) -> None:
        with start_span(
            "chat.persist",
            {
                "persist.feedback": True,
                "conversation_id": payload.conversation_id,
                "message_id": payload.message_id or 0,
                "feedback_type": payload.feedback_type,
            },
        ):
            self.feedback.create(payload)
            self.session.commit()

    def _handle_feedback_message(self, conversation_id: int, message_id: int, request: ChatRequest) -> ChatResponse:
        feedback_type = "useful" if request.message.strip().lower().startswith(("util", "útil")) else "not_useful"
        payload = FeedbackCreate(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=request.user_id,
            feedback_type=feedback_type,
            comment=request.message,
        )
        with start_span(
            "chat.persist",
            {
                "persist.feedback": True,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "feedback_type": feedback_type,
            },
        ):
            self.feedback.create(payload)
            self.session.commit()
        answer = (
            "Gracias. He registrado tu feedback para mejorar futuras respuestas.\n\n"
            "Puedes reformular la pregunta o pedirme otro caso parecido si necesitas una respuesta mas precisa."
        )
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            sources=[],
            related_incidents=[],
            needs_clarification=False,
            clarification_attempt=0,
            should_offer_incident=False,
            adaptive_card=build_feedback_card(),
            fallback_text=answer,
        )

    def _handle_incident_flow(self, conversation_id: int, state: dict, request: ChatRequest) -> dict[str, Any]:
        draft = dict(state.get("pending_incident_draft") or {})
        if state.get("offer_incident") and not draft:
            draft["is_resolved"] = False
            state["offer_incident"] = False
        if draft.get("awaiting_confirmation") and request.message.strip().lower() in {"si", "sí", "confirmo", "confirmar"}:
            payload = validate_draft({key: value for key, value in draft.items() if key != "awaiting_confirmation"})
            created = self._create_incident(payload.model_dump())
            self._trigger_index_incident(created["id"])
            new_state = {"clarification_attempts": 0}
            answer = self._format_incident_created_answer(created)
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": False,
                "clarification_attempt": 0,
                "should_offer_incident": False,
                "adaptive_card": build_incident_confirmation_card(created),
                "fallback_text": answer,
                "created_ticket_id": created["id"],
                "created_ticket_external_id": created["external_id"],
                "_state": new_state,
            }

        draft.update(extract_incident_fields(request.message))
        state["pending_incident_draft"] = draft
        missing = missing_fields(draft)
        if missing:
            next_field = missing[0]
            prompts = {
                "title": "Necesito el titulo de la incidencia. Usa por ejemplo: titulo: Acceso fallido en SafeGate",
                "description": "Necesito la descripcion del problema. Usa: descripcion: ...",
                "department": "Necesito el departamento. Opciones sugeridas: Operaciones, Seguridad, Onboarding, Politicas internas.",
                "category": "Necesito la categoria. Usa: categoria: ...",
                "affected_system": "Necesito el sistema afectado. Ejemplos: LogiCore ERP, RutaNexo, AlmaTrack WMS, SafeGate, OnboardHub, DocuFlow.",
                "is_resolved": "Indica si ya esta resuelta. Puedes escribir: resuelta: si o resuelta: no",
                "impact": "Para incidencias no resueltas necesito el impacto. Usa: impacto: ...",
                "expected_behavior": "Describe el comportamiento esperado. Usa: esperado: ...",
                "actual_behavior": "Describe el comportamiento actual. Usa: actual: ...",
            }
            answer = prompts.get(next_field, f"Necesito el campo {next_field}.")
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": False,
                "clarification_attempt": state.get("clarification_attempts", 0),
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": answer,
                "_state": state,
            }

        draft["awaiting_confirmation"] = True
        state["pending_incident_draft"] = draft
        answer = build_confirmation_text(draft)
        return {
            "conversation_id": conversation_id,
            "answer": answer,
            "sources": [],
            "related_incidents": [],
            "needs_clarification": False,
            "clarification_attempt": state.get("clarification_attempts", 0),
            "should_offer_incident": False,
            "adaptive_card": None,
            "fallback_text": answer,
            "_state": state,
        }

    def _handle_qa_flow(
        self,
        conversation_id: int,
        state: dict,
        request: ChatRequest,
        intent: str,
        user_message_id: int,
        started_at: float,
        *,
        write_retrieval_logs: bool = True,
        retrieval_config: RetrievalConfig | None = None,
        trace_run: Any | None = None,
        stream_token_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        clarification_attempts = int(state.get("clarification_attempts", 0))
        effective_config = retrieval_config or DEFAULT_RETRIEVAL_CONFIG
        recent_messages = self._recent_messages_for_planner(conversation_id, exclude_message_id=user_message_id)
        plan = self._plan_chat_turn(
            message=request.message,
            state=state,
            recent_messages=recent_messages,
            trace_run=trace_run,
        )
        planner_payload = plan.model_dump()
        retrieval_filters = plan.retrieval_filters
        early_signals = detect_query_signals(request.message)

        if plan.should_ask_clarification_first and early_signals.policy_question:
            plan.should_ask_clarification_first = False
            plan.needs_knowledge_index = True
            if not plan.knowledge_index_query.strip():
                plan.knowledge_index_query = request.message
            planner_payload = plan.model_dump()
            planner_payload["planner_override"] = "policy_question_requires_index_search"

        if plan.should_ask_clarification_first:
            clarification_attempts += 1
            state["clarification_attempts"] = clarification_attempts
            if clarification_attempts >= 3:
                state["offer_incident"] = True
                answer = self._build_incident_offer_answer()
                self._trace_formatted_response(
                    trace_run,
                    answer=answer,
                    sources=[],
                    related_incidents=[],
                    conversation_id=conversation_id,
                    actual_behavior="abstain_and_offer_incident_registration",
                    decision={"planner_output": planner_payload, "policy_decision": "offer_incident_after_planner_clarifications"},
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                if write_retrieval_logs:
                    self._write_retrieval_log(
                        conversation_id=conversation_id,
                        message_id=user_message_id,
                        query=request.message,
                        intent=intent,
                        answer=answer,
                        confidence=0.0,
                        latency_ms=latency_ms,
                    )
                return {
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": [],
                    "related_incidents": [],
                    "needs_clarification": False,
                    "clarification_attempt": clarification_attempts,
                    "should_offer_incident": True,
                    "adaptive_card": None,
                    "fallback_text": answer,
                    "_meta": {
                        "retrieved": [],
                        "conversation_memory": [],
                        "confidence": 0.0,
                        "decision": None,
                        "context_chunks": [],
                        "planner_output": planner_payload,
                        "actual_behavior": "abstain_and_offer_incident_registration",
                        "latency_ms": latency_ms,
                    },
                    "_state": state,
                }

            answer = self._build_planner_clarification_answer(plan, clarification_attempts)
            self._trace_formatted_response(
                trace_run,
                answer=answer,
                sources=[],
                related_incidents=[],
                conversation_id=conversation_id,
                actual_behavior="ask_clarification",
                decision={"planner_output": planner_payload, "policy_decision": "ask_clarification_before_retrieval"},
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if write_retrieval_logs:
                self._write_retrieval_log(
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    query=request.message,
                    intent=intent,
                    answer=answer,
                    confidence=0.0,
                    latency_ms=latency_ms,
                )
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": True,
                "clarification_attempt": clarification_attempts,
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": answer,
                "_meta": {
                    "retrieved": [],
                    "conversation_memory": [],
                    "confidence": 0.0,
                    "decision": None,
                    "context_chunks": [],
                    "planner_output": planner_payload,
                    "actual_behavior": "ask_clarification",
                    "latency_ms": latency_ms,
                },
                "_state": state,
            }

        memory_query = (plan.conversation_memory_query or request.message).strip()
        memory_results = self._retrieve_conversation_memory(
            conversation_id=conversation_id,
            query=memory_query,
            exclude_message_id=user_message_id,
            enabled=plan.needs_conversation_memory,
            trace_run=trace_run,
        )
        memory_results, quarantined_memory_ids = self._quarantine_memory(memory_results)
        memory_payload = [memory.to_dict() for memory in memory_results]

        retrieved = []
        quarantined_chunk_ids: list[int] = []
        confidence = 0.0
        knowledge_query = (plan.knowledge_index_query or request.message).strip()
        if plan.needs_knowledge_index:
            with trace_run.child(
                "rag.retrieve",
                run_type="retriever",
                inputs={
                    "question": request.message,
                    "knowledge_index_query": knowledge_query,
                    "retrieval_config": {
                        "top_k": effective_config.top_k,
                        "vector_weight": effective_config.vector_weight,
                        "text_weight": effective_config.text_weight,
                        "vector_candidates": effective_config.vector_candidates,
                        "text_candidates": effective_config.text_candidates,
                    },
                    "retrieval_filters": retrieval_filters.to_dict(),
                    "filter_reason": plan.filter_reason,
                },
            ) if trace_run else RagTrace().start_run("rag.retrieve") as retrieve_run, start_span(
                "chat.retrieve",
                {
                    "conversation_id": conversation_id,
                    "query.length": len(knowledge_query),
                    "retrieval.top_k": effective_config.top_k,
                    "retrieval.vector_weight": effective_config.vector_weight,
                    "retrieval.text_weight": effective_config.text_weight,
                    "retrieval.filters.active": retrieval_filters.active(),
                    "retrieval.filters.source_types": retrieval_filters.source_types,
                    "retrieval.filters.systems": retrieval_filters.affected_systems,
                },
            ) as retrieve_span:
                keyword_query = build_keyword_query(plan.search_keywords, knowledge_query, request.message)
                retrieved = self.retrieve(
                    knowledge_query,
                    retrieval_config=effective_config,
                    retrieval_filters=retrieval_filters,
                    keyword_query=keyword_query,
                )
                self.last_retrieval_debug = {
                    "knowledge_query": knowledge_query,
                    "search_keywords": list(plan.search_keywords),
                    "retrieval_filters": retrieval_filters.to_dict(),
                    **dict(getattr(self.retriever, "last_debug", {}) or {}),
                }
                retrieved, quarantined_chunk_ids = self._quarantine_retrieved(retrieved)
                confidence = self.calculate_confidence(retrieved)
                retrieve_attrs = retrieval_span_attributes(retrieved=retrieved, confidence=confidence)
                retrieve_attrs["security.quarantined_chunk_ids"] = quarantined_chunk_ids
                set_span_attributes(retrieve_span, retrieve_attrs)
                retrieve_run.set_outputs(
                    {
                        "confidence": confidence,
                        "retrieved_chunks": serialize_chunks_for_langsmith(retrieved),
                        "retrieved_count": len(retrieved),
                        "retrieval_filters": retrieval_filters.to_dict(),
                        "filter_reason": plan.filter_reason,
                    }
                )

        incident_source_ids = sorted({item.source_id for item in retrieved if item.source_type == "incident"})
        related_incident_objects = self.incidents.list_related(incident_source_ids)
        related_incidents_by_id = {int(incident.id): incident for incident in related_incident_objects}

        evidence_query = request.message
        with start_span(
            "chat.evidence_gate",
            {
                "conversation_id": conversation_id,
                "retrieved.count": len(retrieved),
                "memory.count": len(memory_results),
            },
        ) as evidence_span:
            query_signals, evidence_assessment = assess_evidence(
                evidence_query,
                retrieved,
                planner_output=planner_payload,
                memory_results=memory_results,
                related_incidents_by_id=related_incidents_by_id,
            )
            set_span_attributes(
                evidence_span,
                {
                    "evidence.mode": evidence_assessment.evidence_mode,
                    "evidence.semantic_confidence": evidence_assessment.semantic_confidence,
                    "evidence.should_block": evidence_assessment.should_block_answer,
                    "evidence.reason_code": evidence_assessment.reason_code,
                    "evidence.allowed_behavior": evidence_assessment.allowed_behavior,
                    "evidence.resolved_incident_count": evidence_assessment.resolved_incident_count,
                    "evidence.unresolved_incident_count": evidence_assessment.unresolved_incident_count,
                    "signals.new_issue": query_signals.new_issue,
                    "signals.resolved_case_request": query_signals.resolved_case_request,
                    "signals.unresolved_case_request": query_signals.unresolved_case_request,
                    "signals.status_request": query_signals.status_request,
                },
            )
        datasource_status = {
            "conversation_memory": {
                "requested": plan.needs_conversation_memory,
                "query": memory_query if plan.needs_conversation_memory else "",
                "result_count": len(memory_results),
            },
            "knowledge_index": {
                "requested": plan.needs_knowledge_index,
                "query": knowledge_query if plan.needs_knowledge_index else "",
                "result_count": len(retrieved),
                "confidence": confidence,
                "retrieval_filters": retrieval_filters.to_dict(),
                "filter_reason": plan.filter_reason,
            },
            "evidence": {
                "mode": evidence_assessment.evidence_mode,
                "semantic_confidence": evidence_assessment.semantic_confidence,
                "allowed_behavior": evidence_assessment.allowed_behavior,
                "blocking_reason": evidence_assessment.blocking_reason,
                "direct_chunk_ids": evidence_assessment.direct_chunk_ids,
            },
        }

        if (not memory_results) and (not retrieved or confidence < self.settings.retrieval_confidence_threshold):
            clarification_attempts += 1
            state["clarification_attempts"] = clarification_attempts
            if clarification_attempts >= 3:
                state["offer_incident"] = True
                answer = self._build_incident_offer_answer()
                self._trace_formatted_response(
                    trace_run,
                    answer=answer,
                    sources=[],
                    related_incidents=[],
                    conversation_id=conversation_id,
                    actual_behavior="abstain_and_offer_incident_registration",
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                if write_retrieval_logs:
                    self._write_retrieval_log(
                        conversation_id=conversation_id,
                        message_id=user_message_id,
                        query=request.message,
                        intent=intent,
                        answer=answer,
                        confidence=confidence,
                        latency_ms=latency_ms,
                    )
                return {
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": [],
                    "related_incidents": [],
                    "needs_clarification": False,
                    "clarification_attempt": clarification_attempts,
                    "should_offer_incident": True,
                    "adaptive_card": None,
                    "fallback_text": answer,
                    "_meta": {
                        "retrieved": retrieved,
                        "conversation_memory": memory_payload,
                        "confidence": confidence,
                        "decision": None,
                        "context_chunks": [],
                        "planner_output": planner_payload,
                        "datasource_status": datasource_status,
                        "actual_behavior": "abstain_and_offer_incident_registration",
                        "latency_ms": latency_ms,
                    },
                    "_state": state,
                }

            full_answer = (
                "Necesito un poco mas de detalle para responder con seguridad.\n\n"
                "Indica el sistema, el proceso o el error exacto.\n\n"
                "Por ejemplo: 'RutaNexo no cierra una ruta aprobada' o 'SafeGate rechaza un acceso temporal'."
            )
            self._trace_formatted_response(
                trace_run,
                answer=full_answer,
                sources=[],
                related_incidents=[],
                conversation_id=conversation_id,
                actual_behavior="ask_clarification",
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if write_retrieval_logs:
                self._write_retrieval_log(
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    query=request.message,
                    intent=intent,
                    answer=full_answer,
                    confidence=confidence,
                    latency_ms=latency_ms,
                )
            return {
                "conversation_id": conversation_id,
                "answer": full_answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": True,
                "clarification_attempt": clarification_attempts,
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": full_answer,
                "_meta": {
                    "retrieved": retrieved,
                    "conversation_memory": memory_payload,
                    "confidence": confidence,
                    "decision": None,
                    "context_chunks": [],
                    "planner_output": planner_payload,
                    "datasource_status": datasource_status,
                    "actual_behavior": "ask_clarification",
                    "latency_ms": latency_ms,
                },
                "_state": state,
            }

        if plan.needs_knowledge_index and self._should_pre_gate_answer(evidence_assessment):
            clarification_attempts += 1
            state["clarification_attempts"] = clarification_attempts
            policy_payload = {
                "planner_output": planner_payload,
                "query_signals": query_signals.to_dict(),
                "evidence_assessment": evidence_assessment.to_dict(),
                "datasource_status": datasource_status,
                "policy_decision": "ask_clarification",
                "policy_override": True,
            }
            if clarification_attempts >= 3:
                state["offer_incident"] = True
                answer = self._build_incident_offer_answer()
                self._trace_formatted_response(
                    trace_run,
                    answer=answer,
                    sources=[],
                    related_incidents=[],
                    conversation_id=conversation_id,
                    actual_behavior="abstain_and_offer_incident_registration",
                    decision=policy_payload,
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                if write_retrieval_logs:
                    self._write_chunked_retrieval_log(
                        conversation_id=conversation_id,
                        message_id=user_message_id,
                        query=request.message,
                        intent=intent,
                        answer=answer,
                        retrieved=retrieved,
                        confidence=confidence,
                        latency_ms=latency_ms,
                        was_answered=False,
                    )
                return {
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": [],
                    "related_incidents": [],
                    "needs_clarification": False,
                    "clarification_attempt": clarification_attempts,
                    "should_offer_incident": True,
                    "adaptive_card": None,
                    "fallback_text": answer,
                    "_meta": {
                        "retrieved": retrieved,
                        "conversation_memory": memory_payload,
                        "confidence": confidence,
                        "decision": None,
                        "context_chunks": [],
                        "planner_output": planner_payload,
                        "datasource_status": datasource_status,
                        "query_signals": query_signals.to_dict(),
                        "evidence_assessment": evidence_assessment.to_dict(),
                        "actual_behavior": "abstain_and_offer_incident_registration",
                        "latency_ms": latency_ms,
                    },
                    "_state": state,
                }

            answer = build_policy_clarification_answer(evidence_assessment, clarification_attempts)
            self._trace_formatted_response(
                trace_run,
                answer=answer,
                sources=[],
                related_incidents=[],
                conversation_id=conversation_id,
                actual_behavior="ask_clarification",
                decision=policy_payload,
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if write_retrieval_logs:
                self._write_chunked_retrieval_log(
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    query=request.message,
                    intent=intent,
                    answer=answer,
                    retrieved=retrieved,
                    confidence=confidence,
                    latency_ms=latency_ms,
                    was_answered=False,
                )
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": True,
                "clarification_attempt": clarification_attempts,
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": answer,
                "_meta": {
                    "retrieved": retrieved,
                    "conversation_memory": memory_payload,
                    "confidence": confidence,
                    "decision": None,
                    "context_chunks": [],
                    "planner_output": planner_payload,
                    "datasource_status": datasource_status,
                    "query_signals": query_signals.to_dict(),
                    "evidence_assessment": evidence_assessment.to_dict(),
                    "actual_behavior": "ask_clarification",
                    "latency_ms": latency_ms,
                },
                "_state": state,
            }

        sources = self._build_visible_sources(retrieved)
        trace_context_chunks = [
            {
                "chunk_id": item.chunk_id,
                "content": item.content,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "metadata": item.metadata,
            }
            for item in retrieved
        ]
        prompt_messages = build_chat_messages(
            question=request.message,
            context_chunks=trace_context_chunks,
            conversation_state={
                **state,
                "_planner_output": planner_payload,
                "_conversation_memory": memory_payload if plan.needs_conversation_memory else None,
                "_datasource_status": datasource_status,
                "_evidence_policy": {
                    "query_signals": query_signals.to_dict(),
                    "evidence_assessment": evidence_assessment.to_dict(),
                },
            },
        )
        with trace_run.child(
            "rag.generate_answer",
            run_type="llm",
            inputs={
                "question": request.message,
                "planner_output": planner_payload,
                "conversation_memory": memory_payload if plan.needs_conversation_memory else [],
                "datasource_status": datasource_status,
                "context_chunks": serialize_context_for_langsmith(trace_context_chunks),
                "messages": prompt_messages,
            },
        ) if trace_run else RagTrace().start_run("rag.generate_answer") as answer_run, start_span(
            "chat.generate_answer",
            {
                "conversation_id": conversation_id,
                "context_chunk_count": len(trace_context_chunks),
                "conversation_memory_count": len(memory_payload),
                "question.length": len(request.message),
                "planner.needs_conversation_memory": plan.needs_conversation_memory,
                "planner.needs_knowledge_index": plan.needs_knowledge_index,
            },
        ) as answer_span:
            evidence_policy = {
                "query_signals": query_signals.to_dict(),
                "evidence_assessment": evidence_assessment.to_dict(),
            }
            if stream_token_callback:
                decision, context_chunks = self.stream_generate_answer(
                    request.message,
                    retrieved,
                    state,
                    on_token=stream_token_callback,
                    evidence_policy=evidence_policy,
                    planner_output=planner_payload,
                    conversation_memory=memory_payload if plan.needs_conversation_memory else None,
                    datasource_status=datasource_status,
                )
            else:
                decision, context_chunks = self.generate_answer(
                    request.message,
                    retrieved,
                    state,
                    evidence_policy=evidence_policy,
                    planner_output=planner_payload,
                    conversation_memory=memory_payload if plan.needs_conversation_memory else None,
                    datasource_status=datasource_status,
                )
            answer_run.set_outputs(
                {
                    "decision": serialize_decision_for_langsmith(decision),
                    "context_chunks": serialize_context_for_langsmith(context_chunks),
                }
            )
            set_span_attributes(
                answer_span,
                {
                    "llm.needs_clarification": decision.needs_clarification,
                    "llm.should_offer_incident": decision.should_offer_incident,
                    "llm.used_chunk_ids": decision.used_chunk_ids,
                    "llm.answer_length": len(decision.answer or ""),
                    "evidence.mode": evidence_assessment.evidence_mode,
                    "evidence.semantic_confidence": evidence_assessment.semantic_confidence,
                    "evidence.allowed_behavior": evidence_assessment.allowed_behavior,
                },
            )
        if plan.needs_knowledge_index and self._should_policy_override_decision(evidence_assessment, decision):
            clarification_attempts += 1
            state["clarification_attempts"] = clarification_attempts
            policy_payload = {
                "planner_output": planner_payload,
                "query_signals": query_signals.to_dict(),
                "evidence_assessment": evidence_assessment.to_dict(),
                "datasource_status": datasource_status,
                "policy_decision": "ask_clarification",
                "policy_override": True,
                "llm_decision": serialize_decision_for_langsmith(decision),
            }
            answer = build_policy_clarification_answer(evidence_assessment, clarification_attempts)
            self._trace_formatted_response(
                trace_run,
                answer=answer,
                sources=[],
                related_incidents=[],
                conversation_id=conversation_id,
                actual_behavior="ask_clarification",
                decision=policy_payload,
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if write_retrieval_logs:
                self._write_chunked_retrieval_log(
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    query=request.message,
                    intent=intent,
                    answer=answer,
                    retrieved=retrieved,
                    confidence=confidence,
                    latency_ms=latency_ms,
                    was_answered=False,
                )
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": True,
                "clarification_attempt": clarification_attempts,
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": answer,
                "_meta": {
                    "retrieved": retrieved,
                    "conversation_memory": memory_payload,
                    "confidence": confidence,
                    "decision": decision,
                    "context_chunks": context_chunks,
                    "planner_output": planner_payload,
                    "datasource_status": datasource_status,
                    "query_signals": query_signals.to_dict(),
                    "evidence_assessment": evidence_assessment.to_dict(),
                    "actual_behavior": "ask_clarification",
                    "latency_ms": latency_ms,
                    "policy_override": True,
                },
                "_state": state,
            }
        if decision.needs_clarification:
            clarification_attempts += 1
            state["clarification_attempts"] = clarification_attempts
            decision_payload = serialize_decision_for_langsmith(decision)
            if clarification_attempts >= 3:
                state["offer_incident"] = True
                answer = self._build_incident_offer_answer()
                self._trace_formatted_response(
                    trace_run,
                    answer=answer,
                    sources=[],
                    related_incidents=[],
                    conversation_id=conversation_id,
                    actual_behavior="abstain_and_offer_incident_registration",
                    decision=decision_payload,
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                if write_retrieval_logs:
                    self.retrieval_logs.create(
                        conversation_id=conversation_id,
                        message_id=user_message_id,
                        query=request.message,
                        detected_intent=intent,
                        retrieved_chunk_ids=[item.chunk_id for item in retrieved],
                        retrieved_source_ids=[item.source_id for item in retrieved],
                        scores={item.chunk_id: item.final_score for item in retrieved},
                        confidence_score=confidence,
                        was_answered=False,
                        tokens_input_estimated=len(request.message.split()) + sum(len(item.content.split()) for item in retrieved),
                        tokens_output_estimated=len(answer.split()),
                        latency_ms=latency_ms,
                        answer=answer,
                    )
                return {
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": [],
                    "related_incidents": [],
                    "needs_clarification": False,
                    "clarification_attempt": clarification_attempts,
                    "should_offer_incident": True,
                    "adaptive_card": None,
                    "fallback_text": answer,
                    "_meta": {
                        "retrieved": retrieved,
                        "conversation_memory": memory_payload,
                        "confidence": confidence,
                        "decision": decision,
                        "context_chunks": context_chunks,
                        "planner_output": planner_payload,
                        "datasource_status": datasource_status,
                        "query_signals": query_signals.to_dict(),
                        "evidence_assessment": evidence_assessment.to_dict(),
                        "actual_behavior": "abstain_and_offer_incident_registration",
                        "latency_ms": latency_ms,
                    },
                    "_state": state,
                }

            answer = self._build_llm_clarification_answer(decision, clarification_attempts)
            self._trace_formatted_response(
                trace_run,
                answer=answer,
                sources=[],
                related_incidents=[],
                conversation_id=conversation_id,
                actual_behavior="ask_clarification",
                decision=decision_payload,
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if write_retrieval_logs:
                self.retrieval_logs.create(
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    query=request.message,
                    detected_intent=intent,
                    retrieved_chunk_ids=[item.chunk_id for item in retrieved],
                    retrieved_source_ids=[item.source_id for item in retrieved],
                    scores={item.chunk_id: item.final_score for item in retrieved},
                    confidence_score=confidence,
                    was_answered=False,
                    tokens_input_estimated=len(request.message.split()) + sum(len(item.content.split()) for item in retrieved),
                    tokens_output_estimated=len(answer.split()),
                    latency_ms=latency_ms,
                    answer=answer,
                )
            return {
                "conversation_id": conversation_id,
                "answer": answer,
                "sources": [],
                "related_incidents": [],
                "needs_clarification": True,
                "clarification_attempt": clarification_attempts,
                "should_offer_incident": False,
                "adaptive_card": None,
                "fallback_text": answer,
                "_meta": {
                    "retrieved": retrieved,
                    "conversation_memory": memory_payload,
                    "confidence": confidence,
                    "decision": decision,
                    "context_chunks": context_chunks,
                    "planner_output": planner_payload,
                    "datasource_status": datasource_status,
                    "query_signals": query_signals.to_dict(),
                    "evidence_assessment": evidence_assessment.to_dict(),
                    "actual_behavior": "ask_clarification",
                    "latency_ms": latency_ms,
                },
                "_state": state,
            }
        if self.last_output_blocked:
            sources = []
            related_incident_objects = []
        state["clarification_attempts"] = 0
        state["offer_incident"] = False
        actual_behavior = (
            "reject_prompt_injection"
            if self.last_output_blocked
            else self._actual_behavior_for_answer(evidence_assessment, decision)
        )
        should_offer_incident = False
        related_incidents = [
            {
                "id": incident.id,
                "external_id": incident.external_id,
                "title": incident.title,
                "status": incident.status,
            }
            for incident in related_incident_objects
        ]
        answer = self._format_answer_for_demo(decision.answer, related_incidents=related_incidents)
        self._trace_formatted_response(
            trace_run,
            answer=answer,
            sources=[source.model_dump() for source in sources],
            related_incidents=related_incidents,
            conversation_id=conversation_id,
            actual_behavior=actual_behavior,
            decision=serialize_decision_for_langsmith(decision),
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if write_retrieval_logs:
            self.retrieval_logs.create(
                conversation_id=conversation_id,
                message_id=user_message_id,
                query=request.message,
                detected_intent=intent,
                retrieved_chunk_ids=[item.chunk_id for item in retrieved],
                retrieved_source_ids=[item.source_id for item in retrieved],
                scores={item.chunk_id: item.final_score for item in retrieved},
                confidence_score=confidence,
                was_answered=not decision.needs_clarification,
                tokens_input_estimated=len(request.message.split()) + sum(len(item.content.split()) for item in retrieved),
                tokens_output_estimated=len(answer.split()),
                latency_ms=latency_ms,
                answer=answer,
            )
        return {
            "conversation_id": conversation_id,
            "answer": answer,
            "sources": [source.model_dump() for source in sources],
            "related_incidents": related_incidents,
            "needs_clarification": decision.needs_clarification,
            "clarification_attempt": 0,
            "should_offer_incident": should_offer_incident,
            "adaptive_card": build_sources_card(answer, [source.model_dump() for source in sources], related_incidents),
            "fallback_text": self._build_sources_fallback(answer, sources, related_incidents),
            "_meta": {
                "retrieved": retrieved,
                "conversation_memory": memory_payload,
                "confidence": confidence,
                "decision": decision,
                "context_chunks": context_chunks,
                "planner_output": planner_payload,
                "datasource_status": datasource_status,
                "query_signals": query_signals.to_dict(),
                "evidence_assessment": evidence_assessment.to_dict(),
                "actual_behavior": actual_behavior,
                "latency_ms": latency_ms,
                "security": {
                    "quarantined_chunk_ids": quarantined_chunk_ids,
                    "quarantined_memory_ids": quarantined_memory_ids,
                    "output_findings": self.last_output_findings,
                    "output_blocked": self.last_output_blocked,
                },
            },
            "_state": state,
        }

    def _blocked_input_response(
        self,
        conversation_id: int,
        state: dict,
        guard: GuardResult,
        *,
        started_at: float,
        trace_run: Any | None,
    ) -> dict[str, Any]:
        answer = build_refusal_answer(guard)
        self._trace_formatted_response(
            trace_run,
            answer=answer,
            sources=[],
            related_incidents=[],
            conversation_id=conversation_id,
            actual_behavior="reject_prompt_injection",
            decision={"policy_decision": "input_guard_block", "input_guard": guard.to_dict()},
        )
        return {
            "conversation_id": conversation_id,
            "answer": answer,
            "sources": [],
            "related_incidents": [],
            "needs_clarification": False,
            "clarification_attempt": int(state.get("clarification_attempts", 0)),
            "should_offer_incident": False,
            "adaptive_card": None,
            "fallback_text": answer,
            "_meta": {
                "retrieved": [],
                "conversation_memory": [],
                "confidence": 0.0,
                "decision": None,
                "context_chunks": [],
                "actual_behavior": "reject_prompt_injection",
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
                "security": {"input_guard": guard.to_dict()},
            },
            "_state": state,
        }

    def _quarantine_retrieved(self, retrieved: list) -> tuple[list, list[int]]:
        """Inyeccion indirecta: un chunk con instrucciones para el modelo no llega al prompt ni a las fuentes."""
        kept, quarantined = [], []
        for item in retrieved:
            if scan_retrieved_text(item.content).blocked:
                quarantined.append(item.chunk_id)
            else:
                kept.append(item)
        if quarantined:
            logger.warning("Chunks en cuarentena por posible prompt injection: %s", quarantined)
        return kept, quarantined

    def _quarantine_memory(self, memories: list) -> tuple[list, list[Any]]:
        kept, quarantined = [], []
        for memory in memories:
            payload = memory.to_dict()
            if scan_retrieved_text(str(payload.get("memory_text", ""))).blocked:
                quarantined.append(payload.get("memory_id"))
            else:
                kept.append(memory)
        return kept, quarantined

    def _format_answer_for_demo(self, answer: str, *, related_incidents: list[dict] | None = None) -> str:
        return self._strip_demo_headings(answer)

    def _strip_demo_headings(self, answer: str) -> str:
        cleaned = (answer or "").strip()
        cleaned = re.sub(r"(?im)^\s*(resumen|detalle|siguiente paso)\s*:?\s*$\n?", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _build_llm_clarification_answer(self, decision: Any, clarification_attempts: int) -> str:
        question = (getattr(decision, "clarification_question", None) or "").strip()
        if not question:
            question = "Necesito un dato mas para responder con seguridad. Indica sistema, proceso, error exacto o contexto operativo."
        return (
            "Necesito aclarar la consulta antes de darte una respuesta fiable.\n\n"
            f"{question}\n\n"
            "Responde con ese dato para continuar."
        )

    def _build_incident_offer_answer(self) -> str:
        return (
            "No tengo evidencia suficiente en el indice para responder con seguridad.\n\n"
            "Con la informacion recuperada no puedo darte un procedimiento fiable sin inventar pasos. "
            "Si quieres, puedo ayudarte a registrar una incidencia no resuelta para dejar trazabilidad."
        )

    def _build_planner_clarification_answer(self, plan: ChatPlan, clarification_attempts: int) -> str:
        systems = ", ".join(plan.mentioned_systems or [])
        if systems:
            detail = (
                f"Para buscar bien en {systems}, necesito el error exacto, pantalla o paso donde ocurre, "
                "proceso afectado y una referencia operativa si aplica."
            )
        else:
            detail = (
                "Necesito un dato mas antes de buscar: sistema, proceso, objetivo o error exacto. "
                "Asi evito darte una respuesta basada en una suposicion."
            )
        return (
            "Necesito aclarar la consulta antes de buscar informacion.\n\n"
            f"{detail}\n\n"
            "Indica sistema, proceso o contexto operativo."
        )

    def _recent_messages_for_planner(self, conversation_id: int, *, exclude_message_id: int, limit: int = 6) -> list[dict[str, Any]]:
        messages = self.messages.list_by_conversation(conversation_id, limit=limit + 1)
        payload: list[dict[str, Any]] = []
        for message in messages:
            if getattr(message, "id", None) == exclude_message_id:
                continue
            payload.append(
                {
                    "id": message.id,
                    "role": message.role,
                    "intent": message.intent,
                    "content": message.content[:1200],
                    "created_at": str(getattr(message, "created_at", "")),
                }
            )
        return payload[-limit:]

    def _plan_chat_turn(
        self,
        *,
        message: str,
        state: dict,
        recent_messages: list[dict[str, Any]],
        trace_run: Any | None,
    ) -> ChatPlan:
        with trace_run.child(
            "rag.plan",
            run_type="llm",
            inputs={"message": message, "recent_messages": recent_messages, "state": state},
        ) if trace_run else RagTrace().start_run("rag.plan") as plan_run, start_span(
            "chat.plan",
            {
                "message.length": len(message),
                "planner.recent_message_count": len(recent_messages),
            },
        ) as span:
            fallback = False
            try:
                plan = self.llm_provider.plan_chat(
                    message=message,
                    recent_messages=recent_messages,
                    conversation_state=state,
                )
            except Exception as exc:
                fallback = True
                logger.warning("No se pudo obtener plan conversacional; usando fallback seguro: %s", exc)
                plan = ChatPlan.fallback(message)

            if plan.needs_knowledge_index and not plan.knowledge_index_query.strip():
                plan.knowledge_index_query = message
            if plan.needs_conversation_memory and not plan.conversation_memory_query.strip():
                plan.conversation_memory_query = message

            payload = plan.model_dump()
            plan_run.set_outputs({"planner_output": payload, "fallback_used": fallback})
            set_span_attributes(
                span,
                {
                    "planner.needs_conversation_memory": plan.needs_conversation_memory,
                    "planner.needs_knowledge_index": plan.needs_knowledge_index,
                    "planner.can_answer_from_conversation_only": plan.can_answer_from_conversation_only,
                    "planner.should_ask_clarification_first": plan.should_ask_clarification_first,
                    "planner.fallback_used": fallback,
                },
            )
            return plan

    def _retrieve_conversation_memory(
        self,
        *,
        conversation_id: int,
        query: str,
        exclude_message_id: int,
        enabled: bool,
        trace_run: Any | None,
    ) -> list[Any]:
        if not enabled:
            return []

        with trace_run.child(
            "rag.memory_retrieve",
            run_type="retriever",
            inputs={"conversation_id": conversation_id, "memory_query": query, "exclude_message_id": exclude_message_id},
        ) if trace_run else RagTrace().start_run("rag.memory_retrieve") as memory_run, start_span(
            "chat.memory_retrieve",
            {
                "conversation_id": conversation_id,
                "query.length": len(query),
                "exclude_message_id": exclude_message_id,
            },
        ) as span:
            try:
                query_embedding = self.llm_provider.embed_texts([query])[0]
                memories = self.memories.search(
                    conversation_id=conversation_id,
                    query_embedding=query_embedding,
                    limit=5,
                    exclude_message_id=exclude_message_id,
                )
            except Exception as exc:
                logger.warning("No se pudo recuperar memoria conversacional: %s", exc)
                memories = []
            payload = [memory.to_dict() for memory in memories]
            memory_run.set_outputs({"conversation_memory": payload, "result_count": len(payload)})
            set_span_attributes(
                span,
                {
                    "memory.result_count": len(payload),
                    "memory.message_ids": [item["message_id"] for item in payload],
                },
            )
            return memories

    def _index_turn_memory(
        self,
        *,
        conversation_id: int,
        user_message: Any,
        assistant_message: Any,
        chat_response: ChatResponse,
        meta: dict[str, Any],
    ) -> None:
        planner_output = meta.get("planner_output") or {}
        query_signals = meta.get("query_signals") or {}
        source_keys = [f"{source.source_type}:{source.source_id}" for source in chat_response.sources]
        entries = [
            (
                user_message,
                {
                    "kind": "user_message",
                    "planner_output": planner_output,
                    "mentioned_systems": planner_output.get("mentioned_systems") or query_signals.get("mentioned_systems") or [],
                },
            ),
            (
                assistant_message,
                {
                    "kind": "assistant_message",
                    "source_keys": source_keys,
                    "needs_clarification": chat_response.needs_clarification,
                    "should_offer_incident": chat_response.should_offer_incident,
                    "planner_output": planner_output,
                },
            ),
        ]
        payloads = []
        for message, metadata in entries:
            summary = summarize_message(message.content)
            memory_text = build_message_memory_text(
                role=message.role,
                content=message.content,
                summary=summary,
                metadata=metadata,
            )
            payloads.append((message, summary, memory_text, metadata))

        try:
            embeddings = self.llm_provider.embed_texts([item[2] for item in payloads])
            for (message, summary, memory_text, metadata), embedding in zip(payloads, embeddings, strict=True):
                self.memories.upsert_for_message(
                    conversation_id=conversation_id,
                    message_id=message.id,
                    role=message.role,
                    memory_text=memory_text,
                    summary=summary,
                    metadata=metadata,
                    embedding=embedding,
                )
            self.session.commit()
        except Exception as exc:
            logger.warning("No se pudo indexar memoria conversacional: %s", exc)
            try:
                self.session.rollback()
            except Exception:
                logger.warning("No se pudo revertir la transaccion fallida de memoria conversacional")

    def _should_pre_gate_answer(self, evidence_assessment: Any) -> bool:
        return bool(
            getattr(evidence_assessment, "should_block_answer", False)
            and getattr(evidence_assessment, "semantic_confidence", 0.0) < 0.55
        )

    def _should_policy_override_decision(self, evidence_assessment: Any, decision: Any) -> bool:
        if getattr(decision, "needs_clarification", False) or getattr(decision, "should_offer_incident", False):
            return False
        if not getattr(evidence_assessment, "requires_direct_evidence", False):
            return False
        if getattr(evidence_assessment, "should_block_answer", False):
            return True
        direct_chunk_ids = set(getattr(evidence_assessment, "direct_chunk_ids", []) or [])
        if not direct_chunk_ids:
            return True
        used_chunk_ids = set(getattr(decision, "used_chunk_ids", []) or [])
        if not used_chunk_ids:
            return True
        return not bool(used_chunk_ids.intersection(direct_chunk_ids))

    def _actual_behavior_for_answer(self, evidence_assessment: Any, decision: Any) -> str:
        if getattr(decision, "needs_clarification", False):
            return "ask_clarification"
        allowed_behavior = getattr(evidence_assessment, "allowed_behavior", "") or ""
        if allowed_behavior in {"say_incident_resolved", "say_incident_unresolved"}:
            return allowed_behavior
        return "answer_with_sources"

    def _trace_response_payload(self, response: ChatResponse) -> dict[str, Any]:
        return {
            "conversation_id": response.conversation_id,
            "answer": response.answer,
            "fallback_text": response.fallback_text,
            "sources": [source.model_dump() for source in response.sources],
            "related_incidents": response.related_incidents,
            "needs_clarification": response.needs_clarification,
            "clarification_attempt": response.clarification_attempt,
            "should_offer_incident": response.should_offer_incident,
            "created_ticket_id": response.created_ticket_id,
            "created_ticket_external_id": response.created_ticket_external_id,
            "message_id": response.message_id,
        }

    def _trace_formatted_response(
        self,
        trace_run,
        *,
        answer: str,
        sources: list[dict],
        related_incidents: list[dict],
        conversation_id: int,
        actual_behavior: str,
        decision: dict[str, Any] | None = None,
    ) -> None:
        with trace_run.child(
            "rag.format_response",
            run_type="chain",
            inputs={
                "decision": decision or {},
                "sources": sources,
                "related_incidents": related_incidents,
                "actual_behavior": actual_behavior,
            },
        ) if trace_run else RagTrace().start_run("rag.format_response") as format_run, start_span(
            "chat.response",
            {
                "conversation_id": conversation_id,
                "source.count": len(sources),
                "related_incident.count": len(related_incidents),
                "actual_behavior": actual_behavior,
            },
        ) as response_span:
            format_run.set_outputs({"answer": answer, "source_count": len(sources)})
            set_span_attributes(response_span, {"answer.length": len(answer)})

    def _format_incident_created_answer(self, created: dict[str, Any]) -> str:
        lines = [
            f"Incidencia registrada e indexada correctamente: {created['external_id']}.",
            "",
            (
                f"Titulo: {created.get('title', 'Sin titulo')} | "
                f"Sistema: {created.get('affected_system', 'N/D')} | "
                f"Estado: {created.get('status', 'N/D')}"
            ),
            "",
            "Ya puedes usar este ticket como referencia en futuras consultas o seguir completando contexto operativo.",
        ]
        return "\n".join(lines)

    def _build_sources_fallback(
        self,
        answer: str,
        sources: list[SourceSnippet],
        related_incidents: list[dict] | None = None,
    ) -> str:
        lines = [answer, "", "Fuentes consultadas"]
        for index, source in enumerate(sources[:5], start=1):
            lines.append(self._format_source_line(source, index=index))
        if related_incidents:
            lines.extend(["", "Incidencias relacionadas"])
            for incident in related_incidents[:3]:
                lines.append(f"- {incident['external_id']}: {incident['title']} ({incident['status']})")
        return "\n".join(lines)

    def _format_source_line(self, source: SourceSnippet, *, index: int) -> str:
        source_type = "Documento" if source.source_type == "document" else "Incidencia"
        title = source.title.strip() or f"{source_type} {source.source_id}"
        detail = source.source_url or source.excerpt.strip()
        if len(detail) > 160:
            detail = detail[:157].rstrip() + "..."
        return f"- [{index}] {source_type} {source.source_id} - {title}: {detail}"

    def _build_visible_sources(self, retrieved: list) -> list[SourceSnippet]:
        best_by_source: dict[tuple[str, int], tuple[int, float, SourceSnippet]] = {}
        for position, item in enumerate(retrieved):
            key = (str(item.source_type), int(item.source_id))
            metadata = item.metadata or {}
            score = float(getattr(item, "final_score", 0.0) or 0.0)
            source = SourceSnippet(
                source_type=item.source_type,
                source_id=item.source_id,
                title=metadata.get("title", f"{item.source_type}-{item.source_id}"),
                source_url=metadata.get("source_url"),
                excerpt=item.content[:260],
                chunk_id=item.chunk_id,
            )
            current = best_by_source.get(key)
            if current is None or score > current[1]:
                best_by_source[key] = (position, score, source)

        ranked = sorted(best_by_source.values(), key=lambda value: (-value[1], value[0]))
        return [source for _, _, source in ranked]

    def _write_retrieval_log(self, *, conversation_id: int, message_id: int, query: str, intent: str, answer: str, confidence: float, latency_ms: int) -> None:
        self.retrieval_logs.create(
            conversation_id=conversation_id,
            message_id=message_id,
            query=query,
            detected_intent=intent,
            retrieved_chunk_ids=[],
            retrieved_source_ids=[],
            scores={},
            confidence_score=confidence,
            was_answered=False,
            tokens_input_estimated=len(query.split()),
            tokens_output_estimated=len(answer.split()),
            latency_ms=latency_ms,
            answer=answer,
        )

    def _write_chunked_retrieval_log(
        self,
        *,
        conversation_id: int,
        message_id: int,
        query: str,
        intent: str,
        answer: str,
        retrieved: list,
        confidence: float,
        latency_ms: int,
        was_answered: bool,
    ) -> None:
        self.retrieval_logs.create(
            conversation_id=conversation_id,
            message_id=message_id,
            query=query,
            detected_intent=intent,
            retrieved_chunk_ids=[item.chunk_id for item in retrieved],
            retrieved_source_ids=[item.source_id for item in retrieved],
            scores={item.chunk_id: item.final_score for item in retrieved},
            confidence_score=confidence,
            was_answered=was_answered,
            tokens_input_estimated=len(query.split()) + sum(len(item.content.split()) for item in retrieved),
            tokens_output_estimated=len(answer.split()),
            latency_ms=latency_ms,
            answer=answer,
        )

    def _create_incident(self, payload: dict) -> dict:
        headers = {"x-app-shared-secret": self.settings.app_shared_secret}
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{self.settings.custom_incidents_api_base_url}/incidents", json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def _trigger_index_incident(self, incident_id: int) -> None:
        headers = {"x-app-shared-secret": self.settings.app_shared_secret}
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{self.settings.indexer_api_base_url}/index/incident/{incident_id}", headers=headers)
            response.raise_for_status()
