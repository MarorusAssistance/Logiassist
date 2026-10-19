from __future__ import annotations

import argparse

from sqlalchemy import select

from internal_assistant.chat.memory import build_message_memory_text, summarize_message
from internal_assistant.db import session_scope
from internal_assistant.llm import build_default_provider
from internal_assistant.models import ConversationMemory, Message
from internal_assistant.repositories.memory import ConversationMemoryRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa memoria vectorial para mensajes existentes.")
    parser.add_argument("--limit", type=int, default=500, help="numero maximo de mensajes a procesar")
    args = parser.parse_args()

    provider = build_default_provider()
    processed = 0

    with session_scope() as session:
        repository = ConversationMemoryRepository(session)
        existing_message_ids = set(session.execute(select(ConversationMemory.message_id)).scalars().all())
        messages = session.execute(select(Message).order_by(Message.id).limit(args.limit)).scalars().all()
        pending = [message for message in messages if message.id not in existing_message_ids]

        payloads = []
        for message in pending:
            summary = summarize_message(message.content)
            metadata = {"kind": f"{message.role}_message", "intent": message.intent}
            memory_text = build_message_memory_text(
                role=message.role,
                content=message.content,
                summary=summary,
                metadata=metadata,
            )
            payloads.append((message, summary, memory_text, metadata))

        embeddings = provider.embed_texts([payload[2] for payload in payloads]) if payloads else []
        for (message, summary, memory_text, metadata), embedding in zip(payloads, embeddings, strict=True):
            repository.upsert_for_message(
                conversation_id=message.conversation_id,
                message_id=message.id,
                role=message.role,
                memory_text=memory_text,
                summary=summary,
                metadata=metadata,
                embedding=embedding,
            )
            processed += 1

    print(f"Conversation memories indexed: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
