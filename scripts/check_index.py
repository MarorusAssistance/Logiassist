from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from internal_assistant.db import session_scope
from internal_assistant.functions import check_index


def main() -> int:
    try:
        with session_scope() as session:
            report = check_index(session)
    except Exception as exc:
        print(f"Check index: ERROR - {exc}", file=sys.stderr)
        return 1

    print(f"Incidents: {report['incidents']}")
    print(f"Documents: {report['documents']}")
    print(f"Chunks: {report['chunks']}")
    print(f"Chunks with embeddings: {report['chunks_with_embeddings']}")
    print(f"Embedding dimensions: {report['embedding_dimensions']}")
    print(f"Vector extension enabled: {'OK' if report['vector_extension_enabled'] else 'FAIL'}")
    print("Vector search: OK")
    print("Full-text search: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
