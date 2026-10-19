from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from internal_assistant.db import session_scope
from internal_assistant.functions import rebuild_index


def main() -> int:
    started_at = time.perf_counter()
    try:
        with session_scope() as session:
            result = rebuild_index(session)
    except Exception as exc:
        print(f"Rebuild: ERROR - {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    print(f"Incidents read: {result['incidents_read']}")
    print(f"Documents read: {result['documents_read']}")
    print(f"Chunks generated: {result['total_chunks']}")
    print(f"Chunks with embeddings: {result['chunks_with_embeddings']}")
    print(f"Embedding dimensions: {result['embedding_dimensions']}")
    print(f"Tiempo total: {elapsed_ms} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
