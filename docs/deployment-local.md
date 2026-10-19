# Despliegue local

## Objetivo

Levantar el asistente completo en local con PostgreSQL, dataset sintetico, indice RAG y endpoints HTTP listos para demo o desarrollo.

## Requisitos

- Python 3.12
- `uv`
- Docker Desktop o un PostgreSQL local equivalente
- opcionalmente un provider local compatible con OpenAI como LM Studio

## Variables

1. Copia `.env.example` a `.env`.
2. Elige uno de estos perfiles:

### Mock barato

```env
LLM_PROVIDER=mock
EMBEDDINGS_PROVIDER=mock
EMBEDDING_DIMENSIONS=512
```

### OpenAI

```env
LLM_PROVIDER=openai
EMBEDDINGS_PROVIDER=openai
OPENAI_API_KEY=sk-...
CHAT_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=512
```

### Provider local compatible

```env
LLM_PROVIDER=openai_compatible
EMBEDDINGS_PROVIDER=openai_compatible
LLM_BASE_URL=http://127.0.0.1:1234/v1
LLM_API_KEY=local-dev-key
CHAT_MODEL=<tu-modelo-chat>
EMBEDDING_MODEL=<tu-modelo-embedding>
EMBEDDING_DIMENSIONS=<dimension-real>
```

## Flujo completo

```bash
python -m uv sync
docker compose up -d postgres
python -m uv run python scripts/init_db.py
python -m uv run python scripts/validate_seed_data.py
python -m uv run python scripts/seed_db.py
python -m uv run python scripts/rebuild_index.py
python -m uv run python scripts/check_index.py
./scripts/demo_prep.sh local
./scripts/demo_health_check.sh local
```

## Arranque de servicios HTTP

```bash
python -m uv run uvicorn main:app --app-dir app-service --host 0.0.0.0 --port 8000
python -m uv run uvicorn local_main:app --app-dir functions/custom-incidents-api-function --host 0.0.0.0 --port 7071
python -m uv run uvicorn local_main:app --app-dir functions/indexer-function --host 0.0.0.0 --port 7072
```

## Smoke test sin Teams

La forma mas comoda para demo es abrir:

```text
http://localhost:8000/demo
```

La pantalla permite comprobar `health`, enviar preguntas a `/api/chat`, ver fuentes, enviar feedback y resetear la conversacion. No depende de Teams ni de Bot Framework.

Tambien puedes llamar la API directamente:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u-demo",
    "message": "Como solicito acceso temporal a SafeGate para personal externo?"
  }'
```

## Endpoints utiles

- `http://localhost:8000/api/health`
- `http://localhost:8000/api/health/deep`
- `http://localhost:8000/api/chat`
- `http://localhost:8000/demo`
- `http://localhost:7071/health`
- `http://localhost:7072/health`

## Reranker local opcional

Para reordenar candidatos con un BGE cross-encoder local:

```bash
python -m uv run --extra reranker python scripts/serve_reranker.py
```

Activalo en `.env`:

```env
RERANKER_ENABLED=true
RERANKER_BASE_URL=http://127.0.0.1:8091
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```

Si vienes de una base ya creada, ejecuta migraciones y reconstruye chunks para poblar las columnas de metadata:

```bash
python -m uv run python scripts/init_db.py
python -m uv run python scripts/rebuild_index.py
```

## Problemas habituales

- `No hay chunks en el indice`: vuelve a ejecutar `scripts/rebuild_index.py`
- `column chunks.source_title does not exist`: ejecuta `scripts/init_db.py` y luego `scripts/rebuild_index.py`
- `Reranker unavailable`: el backend sigue con ranking hibrido; revisa que `scripts/serve_reranker.py` este levantado
- `different vector dimensions`: alinea `EMBEDDING_DIMENSIONS` con el modelo usado para indexar
- `Request timed out` con provider local: revisa `LLM_BASE_URL`, timeout y tamano del modelo
- `DATABASE_URL` invalido: confirma que PostgreSQL local acepta las credenciales del `.env`
