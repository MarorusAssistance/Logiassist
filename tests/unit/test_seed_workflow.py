from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from scripts import seed_db
from internal_assistant.functions import indexer as indexer_module
from internal_assistant.models import Document, Incident
from internal_assistant.seed_data import build_seed_documents, build_seed_tickets


class FakeResult:
    def __init__(self, *, scalars_data=None, scalar_value=None):
        self._scalars_data = scalars_data or []
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._scalars_data)

    def mappings(self):
        return self

    def scalar_one(self):
        return self._scalar_value


class FakeSession:
    def __init__(self, *, documents=None, incidents=None):
        self.documents = {item.id: item for item in (documents or [])}
        self.incidents = {item.id: item for item in (incidents or [])}
        self.chunks = {}
        self.next_chunk_id = 1
        self.sequence_calls: list[str] = []

    def get(self, model, identifier):
        if model is Document:
            return self.documents.get(identifier)
        if model is Incident:
            return self.incidents.get(identifier)
        return None

    def add(self, instance):
        if isinstance(instance, Document):
            self.documents[instance.id] = instance
        elif isinstance(instance, Incident):
            self.incidents[instance.id] = instance

    def flush(self):
        return None

    def commit(self):
        return None

    def execute(self, statement, params=None):
        if hasattr(statement, "text"):
            sql = statement.text.lower()
            if "delete from chunks" in sql:
                self.chunks.clear()
                return FakeResult(scalar_value=0)
            if "update chunks set full_text_tsvector" in sql:
                return FakeResult(scalar_value=0)
            if "setval" in sql:
                self.sequence_calls.append(statement.text)
                return FakeResult(scalar_value=0)
            if "vector_dims(embedding)" in sql:
                dimensions = 0
                for chunk in self.chunks.values():
                    if chunk.embedding is not None:
                        dimensions = len(chunk.embedding)
                        break
                return FakeResult(scalar_value=dimensions)
            if "from chunks" in sql and "embedding <=> embedding" in sql:
                rows = []
                for chunk in self.chunks.values():
                    if chunk.embedding is None:
                        continue
                    rows.append(
                        {
                            "id": chunk.id,
                            "source_type": chunk.source_type,
                            "source_id": chunk.source_id,
                            "content": chunk.content,
                            "metadata": chunk.metadata_,
                            "score": 1.0,
                        }
                    )
                return FakeResult(scalars_data=rows[:5])
            if "from pg_extension" in sql and "extname = 'vector'" in sql:
                return FakeResult(scalar_value=True)
            raise AssertionError(f"SQL no esperado: {statement.text}")

        query = str(statement).lower()
        if "from documents" in query and "documents.id" in query and "count(" not in query:
            return FakeResult(scalars_data=sorted(self.documents))
        if "from incidents" in query and "incidents.id" in query and "count(" not in query:
            return FakeResult(scalars_data=sorted(self.incidents))
        if "count(documents.id)" in query:
            return FakeResult(scalar_value=len(self.documents))
        if "count(incidents.id)" in query:
            return FakeResult(scalar_value=len(self.incidents))
        if "count(chunks.id)" in query and "is not null" in query:
            return FakeResult(scalar_value=sum(1 for chunk in self.chunks.values() if chunk.embedding is not None))
        if "count(chunks.id)" in query:
            return FakeResult(scalar_value=len(self.chunks))
        raise AssertionError(f"Consulta no esperada: {statement}")


class FakeChunkRepository:
    def __init__(self, session):
        self.session = session

    def delete_by_source(self, source_type, source_id):
        keys = [
            key for key in self.session.chunks
            if key[0] == source_type and key[1] == source_id
        ]
        for key in keys:
            del self.session.chunks[key]

    def upsert(self, chunk):
        key = (chunk.source_type, chunk.source_id, chunk.chunk_index)
        existing = self.session.chunks.get(key)
        if existing:
            existing.content = chunk.content
            existing.embedding = chunk.embedding
            existing.metadata_ = chunk.metadata_
            existing.full_text_tsvector = chunk.full_text_tsvector
            return existing
        chunk.id = self.session.next_chunk_id
        self.session.next_chunk_id += 1
        self.session.chunks[key] = chunk
        return chunk

    def vector_search(self, embedding, limit=15, filters=None):
        rows = []
        for chunk in self.session.chunks.values():
            rows.append(
                {
                    "id": chunk.id,
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata_,
                    "score": 1.0,
                }
            )
        return rows[:limit]

    def text_search(self, query, limit=15, filters=None):
        terms = [term.lower() for term in query.split()]
        rows = []
        for chunk in self.session.chunks.values():
            content = chunk.content.lower()
            if any(term in content for term in terms):
                rows.append(
                    {
                        "id": chunk.id,
                        "source_type": chunk.source_type,
                        "source_id": chunk.source_id,
                        "content": chunk.content,
                        "metadata": chunk.metadata_,
                        "score": 1.0,
                    }
                )
        return rows[:limit]


class FakeProvider:
    def embed_texts(self, texts):
        return [[float((len(text) % 7) + 1)] * 512 for text in texts]


def _incident_namespace(payload: dict) -> SimpleNamespace:
    item = dict(payload)
    item.setdefault("resolution", None)
    item.setdefault("resolved_at", None)
    return SimpleNamespace(**item)


def test_seed_db_is_idempotent(monkeypatch):
    tickets = build_seed_tickets()
    documents = build_seed_documents()
    session = FakeSession()

    @contextmanager
    def fake_scope():
        yield session

    monkeypatch.setattr(seed_db, "session_scope", fake_scope)
    monkeypatch.setattr(seed_db, "load_seed_data", lambda data_dir=None: (tickets, documents))

    first = seed_db.seed_database()
    second = seed_db.seed_database()

    assert first.incidents.created == 100
    assert first.documents.created == 20
    assert second.incidents.skipped == 100
    assert second.documents.skipped == 20
    assert len(session.sequence_calls) == 4


def test_rebuild_index_generates_chunks(monkeypatch):
    documents = [SimpleNamespace(**build_seed_documents()[0]), SimpleNamespace(**build_seed_documents()[8])]
    incidents = [_incident_namespace(build_seed_tickets()[0]), _incident_namespace(build_seed_tickets()[90])]
    session = FakeSession(documents=documents, incidents=incidents)
    provider = FakeProvider()

    monkeypatch.setattr(indexer_module, "ChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(
        indexer_module,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_dimensions=512,
            embeddings_provider="mock",
            llm_provider="mock",
            llm_base_url="",
            openai_api_key="",
        ),
    )

    report = indexer_module.rebuild_index(session, llm_provider=provider)

    assert report["documents_read"] == 2
    assert report["incidents_read"] == 2
    assert report["total_chunks"] > 0
    assert report["chunks_with_embeddings"] == report["total_chunks"]
    assert report["embedding_dimensions"] == 512
    assert report["vector_extension_enabled"] is True


def test_check_index_detects_valid_index(monkeypatch):
    documents = [SimpleNamespace(**build_seed_documents()[8])]
    incidents = [_incident_namespace(build_seed_tickets()[0])]
    session = FakeSession(documents=documents, incidents=incidents)
    provider = FakeProvider()

    monkeypatch.setattr(indexer_module, "ChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(
        indexer_module,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_dimensions=512,
            embeddings_provider="mock",
            llm_provider="mock",
            llm_base_url="",
            openai_api_key="",
        ),
    )
    indexer_module.rebuild_index(session, llm_provider=provider)

    report = indexer_module.check_index(session, llm_provider=provider)

    assert report["incidents"] == 1
    assert report["documents"] == 1
    assert report["chunks"] > 0
    assert report["embedding_dimensions"] == 512
    assert report["vector_extension_enabled"] is True
