from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete

from internal_assistant.db import session_scope
from internal_assistant.models import Chunk, Conversation, ConversationMemory, Document, Feedback, Incident, Message, RetrievalLog


def _confirm_reset() -> bool:
    answer = input("Esto borrará datos locales de desarrollo. Escribe 'yes' para continuar: ").strip().lower()
    return answer == "yes"


def main() -> int:
    parser = argparse.ArgumentParser(description="Resetea la base de datos local de desarrollo.")
    parser.add_argument("--yes", action="store_true", help="omite la confirmación interactiva")
    args = parser.parse_args()

    if not args.yes and not _confirm_reset():
        print("Reset cancelado.")
        return 1

    with session_scope() as session:
        session.execute(delete(Chunk))
        session.execute(delete(ConversationMemory))
        session.execute(delete(Feedback))
        session.execute(delete(RetrievalLog))
        session.execute(delete(Message))
        session.execute(delete(Conversation))
        session.execute(delete(Incident))
        session.execute(delete(Document))

    print("Reset local completado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
