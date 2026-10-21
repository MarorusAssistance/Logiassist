from __future__ import annotations

import hashlib
import json
from queue import Queue
import sys
from threading import Thread
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from botbuilder.core import ActivityHandler, BotFrameworkAdapter, BotFrameworkAdapterSettings, CardFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

APP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = APP_ROOT / "src"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from internal_assistant.chat import ChatService
from internal_assistant.chat.titles import fallback_title, generate_title
from internal_assistant.llm import build_default_provider
from internal_assistant.models import Conversation
from internal_assistant.config import get_settings
from internal_assistant.db import get_session, session_scope
from internal_assistant.observability import configure_logging, get_logger, start_span
from internal_assistant.observability.tracing import set_span_attributes, user_hash
from internal_assistant.observability.usage import metering
from internal_assistant.repositories import DocumentRepository, IncidentRepository
from internal_assistant.repositories.chat import ConversationRepository, MessageRepository
from internal_assistant.runtime import assert_runtime_settings, build_health_report
from internal_assistant.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationMessage,
    ConversationRename,
    ConversationSummary,
    DocumentRead,
    FeedbackCreate,
    IncidentRead,
)
from internal_assistant.security.usage_limits import UsageLimiter, client_ip, client_key, total_tokens
from internal_assistant.teams import coerce_activity_input

configure_logging()
settings = get_settings()
logger = get_logger(__name__)
STATIC_ROOT = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    assert_runtime_settings(settings, require_bot=settings.app_env.strip().lower() in {"dev", "demo"})
    yield


app = FastAPI(title="internal-assistant-mvp", version="0.1.0", lifespan=lifespan)


class RevalidatedStaticFiles(StaticFiles):
    """Obliga al navegador a revalidar (ETag) para que nunca use un app.js viejo con un index.html nuevo."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if STATIC_ROOT.exists():
    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_ROOT), name="static")

adapter = BotFrameworkAdapter(
    BotFrameworkAdapterSettings(
        settings.microsoft_app_id,
        settings.microsoft_app_password,
        channel_auth_tenant=settings.microsoft_app_tenant_id or None,
    )
)

if settings.allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class TeamsAssistantBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        message = coerce_activity_input(activity.text, activity.value)
        if not message:
            await turn_context.send_activity(
                "No he podido interpretar el mensaje o la accion enviada. Intenta de nuevo con texto o con una tarjeta valida."
            )
            return

        with session_scope() as session:
            service = ChatService(session)
            response = service.handle_chat(
                ChatRequest(
                    user_id=activity.from_property.id if activity.from_property else "teams-user",
                    message=message,
                    channel=activity.channel_id or "msteams",
                    teams_conversation_id=activity.conversation.id if activity.conversation else None,
                )
            )

        if response.adaptive_card:
            reply = Activity(
                type=ActivityTypes.message,
                text=response.fallback_text,
                attachments=[CardFactory.adaptive_card(response.adaptive_card)],
            )
            await turn_context.send_activity(reply)
            return

        await turn_context.send_activity(response.fallback_text)


bot = TeamsAssistantBot()


async def on_turn_error(turn_context: TurnContext, error: Exception) -> None:
    logger.error("Bot Framework processing error: %s", error)
    await turn_context.send_activity(
        "Se ha producido un error procesando tu mensaje. Intenta de nuevo o usa /api/chat para validar el servicio."
    )


adapter.on_turn_error = on_turn_error


DbSession = Annotated[Session, Depends(get_session)]


def _enforce_usage_limits(request: Request) -> str:
    """Rechaza con 429 si el cliente o la demo han superado sus limites; devuelve la clave del cliente."""
    key = client_key(client_ip(request.headers, request.client.host if request.client else None))
    with session_scope() as limits_session:
        decision = UsageLimiter(limits_session, settings).check(key)
    if not decision.allowed:
        logger.warning("Limite de uso alcanzado (%s) para cliente %s", decision.reason, key[:8])
        raise HTTPException(
            status_code=429,
            detail=decision.message,
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    return key


def _schedule_title(conversation_id: int, question: str, answer: str) -> None:
    """Titula la conversacion en segundo plano tras su primer intercambio (sin anadir latencia)."""

    def worker() -> None:
        try:
            with session_scope() as title_session:
                conversation = title_session.get(Conversation, conversation_id)
                if conversation is None or conversation.title:
                    return
                conversation.title = generate_title(build_default_provider(), settings, question, answer)
                title_session.commit()
        except Exception as exc:
            logger.warning("No se pudo titular la conversacion %s: %s", conversation_id, exc)

    Thread(target=worker, daemon=True).start()


def _record_usage(key: str, meter) -> None:
    try:
        with session_scope() as limits_session:
            UsageLimiter(limits_session, settings).record(key, total_tokens(meter))
    except Exception as exc:  # El registro de uso nunca debe romper la respuesta.
        logger.warning("No se pudo registrar el uso de la peticion: %s", exc)


def _demo_page() -> str:
    """index.html con ?v=<hash> en app.js y styles.css: cada despliegue cambia la URL de los assets."""
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    for asset in ("app.js", "styles.css"):
        digest = hashlib.sha256((STATIC_ROOT / asset).read_bytes()).hexdigest()[:12]
        html = html.replace(f"/static/{asset}\"", f"/static/{asset}?v={digest}\"")
    return html


DEMO_PAGE = _demo_page() if STATIC_ROOT.exists() else ""


@app.get("/", include_in_schema=False)
def demo_index() -> HTMLResponse:
    return HTMLResponse(DEMO_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/demo", include_in_schema=False)
def demo_ui() -> HTMLResponse:
    return HTMLResponse(DEMO_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health(session: DbSession) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/health/deep")
def health_deep(session: DbSession) -> JSONResponse:
    payload, ok = build_health_report(session, settings)
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, session: DbSession, request: Request) -> ChatResponse:
    limit_key = _enforce_usage_limits(request)
    with start_span(
        "api.chat",
        {
            "channel": payload.channel,
            "conversation_id": payload.conversation_id or 0,
            "user_id_hash": user_hash(payload.user_id),
            "message.length": len(payload.message),
        },
    ) as span:
        service = ChatService(session)
        with metering() as meter:
            response = service.handle_chat(payload)
        _record_usage(limit_key, meter)
        _schedule_title(response.conversation_id, payload.message, response.answer)
        set_span_attributes(
            span,
            {
                "response.conversation_id": response.conversation_id,
                "response.message_id": response.message_id or 0,
                "response.answer_length": len(response.answer),
                "response.source_count": len(response.sources),
                "response.needs_clarification": response.needs_clarification,
            },
        )
        return response


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    limit_key = _enforce_usage_limits(request)

    def events():
        event_queue: Queue[tuple[str, dict] | None] = Queue()

        def push_token(text: str) -> None:
            event_queue.put(("token", {"text": text}))

        def worker() -> None:
            try:
                with session_scope() as session, metering() as meter:
                    service = ChatService(session)
                    response = service.handle_chat(payload, stream_token_callback=push_token)
                _record_usage(limit_key, meter)
                _schedule_title(response.conversation_id, payload.message, response.answer)
                response_payload = response.model_dump()
                event_queue.put(
                    (
                        "sources",
                        {
                            "sources": [source.model_dump() for source in response.sources],
                            "related_incidents": response.related_incidents,
                        },
                    )
                )
                event_queue.put(("final", response_payload))
            except Exception as exc:
                logger.exception("Streaming chat failed: %s", exc)
                event_queue.put(
                    (
                        "error",
                        {
                            "message": "No se pudo completar la respuesta en streaming. Prueba de nuevo o usa /api/chat.",
                        },
                    )
                )
            finally:
                event_queue.put(None)

        Thread(target=worker, daemon=True).start()
        while True:
            item = event_queue.get()
            if item is None:
                break
            event_name, event_payload = item
            yield _sse_event(event_name, event_payload)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


UserIdHeader = Annotated[str, Header(alias="X-User-Id", min_length=8, max_length=128)]


def _owned_conversation(session: Session, conversation_id: int, user_id: str):
    conversation = ConversationRepository(session).get_for_user(conversation_id, user_id)
    if conversation is None:
        # Mismo 404 exista o no: no se revela si el id pertenece a otro usuario.
        raise HTTPException(status_code=404, detail="Conversacion no encontrada")
    return conversation


def _summary(conversation, message_count: int, first_question: str = "") -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title or fallback_title(first_question),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
    )


@app.get("/api/conversations", response_model=list[ConversationSummary])
def list_conversations(session: DbSession, user_id: UserIdHeader) -> list[ConversationSummary]:
    return [_summary(conversation, count) for conversation, count in ConversationRepository(session).list_for_user(user_id)]


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, session: DbSession, user_id: UserIdHeader) -> ConversationDetail:
    conversation = _owned_conversation(session, conversation_id, user_id)
    messages = MessageRepository(session).list_all(conversation.id)
    first_question = next((message.content for message in messages if message.role == "user"), "")
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title or fallback_title(first_question),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ConversationMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                meta=message.meta,
            )
            for message in messages
        ],
    )


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: int, payload: ConversationRename, session: DbSession, user_id: UserIdHeader
) -> ConversationSummary:
    repository = ConversationRepository(session)
    conversation = repository.rename(_owned_conversation(session, conversation_id, user_id), payload.title)
    session.commit()
    count = len(MessageRepository(session).list_all(conversation.id))
    return _summary(conversation, count)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, session: DbSession, user_id: UserIdHeader) -> Response:
    repository = ConversationRepository(session)
    repository.delete(_owned_conversation(session, conversation_id, user_id))
    session.commit()
    return Response(status_code=204)


@app.post("/api/messages")
async def messages(request: Request) -> Response:
    body = await request.json()
    activity = Activity().deserialize(body)
    if (activity.type or "").lower() not in {"message", "invoke"}:
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "Actividad no soportada en el MVP"})

    try:
        invoke_response = await adapter.process_activity(activity, request.headers.get("Authorization", ""), bot.on_turn)
    except PermissionError as exc:
        logger.warning("Bot Framework auth error: %s", exc)
        return JSONResponse(status_code=401, content={"detail": "Unauthorized Bot Framework request"})
    if invoke_response:
        return JSONResponse(status_code=invoke_response.status, content=invoke_response.body)
    return Response(status_code=200)


@app.get("/api/tickets", response_model=list[IncidentRead])
def get_tickets(session: DbSession) -> list[IncidentRead]:
    return [IncidentRead.model_validate(item) for item in IncidentRepository(session).list()]


@app.get("/api/tickets/{ticket_id}", response_model=IncidentRead)
def get_ticket(ticket_id: int, session: DbSession) -> IncidentRead:
    incident = IncidentRepository(session).get(ticket_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return IncidentRead.model_validate(incident)


@app.get("/api/documents", response_model=list[DocumentRead])
def get_documents(session: DbSession) -> list[DocumentRead]:
    return [DocumentRead.model_validate(item) for item in DocumentRepository(session).list()]


@app.post("/api/feedback")
def save_feedback(payload: FeedbackCreate, session: DbSession) -> dict:
    service = ChatService(session)
    service.save_feedback(payload)
    return {"status": "stored"}
