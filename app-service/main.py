from __future__ import annotations

import json
from queue import Queue
import sys
from threading import Thread
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from botbuilder.core import ActivityHandler, BotFrameworkAdapter, BotFrameworkAdapterSettings, CardFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
from internal_assistant.config import get_settings
from internal_assistant.db import get_session, session_scope
from internal_assistant.observability import configure_logging, get_logger, start_span
from internal_assistant.observability.tracing import set_span_attributes, user_hash
from internal_assistant.repositories import DocumentRepository, IncidentRepository
from internal_assistant.runtime import assert_runtime_settings, build_health_report
from internal_assistant.schemas import ChatRequest, ChatResponse, DocumentRead, FeedbackCreate, IncidentRead
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
if STATIC_ROOT.exists():
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

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


@app.get("/", include_in_schema=False)
def demo_index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/demo", include_in_schema=False)
def demo_ui() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/health")
def health(session: DbSession) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/health/deep")
def health_deep(session: DbSession) -> JSONResponse:
    payload, ok = build_health_report(session, settings)
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, session: DbSession) -> ChatResponse:
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
        response = service.handle_chat(payload)
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
def chat_stream(payload: ChatRequest) -> StreamingResponse:
    def events():
        event_queue: Queue[tuple[str, dict] | None] = Queue()

        def push_token(text: str) -> None:
            event_queue.put(("token", {"text": text}))

        def worker() -> None:
            try:
                with session_scope() as session:
                    service = ChatService(session)
                    response = service.handle_chat(payload, stream_token_callback=push_token)
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
