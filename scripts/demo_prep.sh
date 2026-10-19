#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_demo_common.sh"

TARGET="${1:-local}"
load_demo_context "${TARGET}"

[[ -n "${DATABASE_URL:-}" ]] || fail "Falta DATABASE_URL para preparar la demo"
check_provider_requirements

print_info "Preparando demo para target '${DEMO_TARGET}'"
print_info "API base: ${DEMO_API_BASE_URL}"

provider="$(resolved_provider_name)"
if [[ "${provider}" == "openai_compatible" ]]; then
  llm_health_url="${LLM_BASE_URL%/}/models"
  print_info "Comprobando endpoint LLM local/compatible: ${llm_health_url}"
  if ! run_curl -fsS --max-time 15 "${llm_health_url}" >/dev/null; then
    fail "El endpoint LLM compatible no responde: ${llm_health_url}"
  fi
fi

if [[ "${RERANKER_ENABLED:-false}" == "true" ]]; then
  reranker_health_url="${RERANKER_BASE_URL:-http://127.0.0.1:8091}/health"
  print_info "Comprobando reranker local: ${reranker_health_url}"
  if ! run_curl -fsS --max-time 15 "${reranker_health_url}" >/dev/null; then
    fail "RERANKER_ENABLED=true pero el reranker no responde: ${reranker_health_url}"
  fi
fi

env_pairs=(
  "DATABASE_URL=${DATABASE_URL}"
  "APP_ENV=${APP_ENV:-}"
  "LLM_PROVIDER=${LLM_PROVIDER:-}"
  "EMBEDDINGS_PROVIDER=${EMBEDDINGS_PROVIDER:-}"
  "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
  "CHAT_MODEL=${CHAT_MODEL:-}"
  "EMBEDDING_MODEL=${EMBEDDING_MODEL:-}"
  "EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS:-}"
  "LLM_BASE_URL=${LLM_BASE_URL:-}"
  "LLM_API_KEY=${LLM_API_KEY:-}"
  "RERANKER_ENABLED=${RERANKER_ENABLED:-false}"
  "RERANKER_BASE_URL=${RERANKER_BASE_URL:-http://127.0.0.1:8091}"
)

run_uv_python_inline_env '
from sqlalchemy import text

from internal_assistant.config import get_settings
from internal_assistant.db import session_scope
from internal_assistant.llm import resolve_provider_name
from internal_assistant.models import Chunk, Document, Incident

settings = get_settings()

with session_scope() as session:
    session.execute(text("SELECT 1"))
    incidents = session.query(Incident).count()
    documents = session.query(Document).count()
    chunks = session.query(Chunk).count()
    vector_enabled = bool(
        session.execute(text("SELECT 1 FROM pg_extension WHERE extname = '\''vector'\''")).scalar()
    )

print(f"Environment: {settings.app_env}")
print(f"Resolved provider: {resolve_provider_name(settings)}")
print(f"Chat model: {settings.chat_model}")
print(f"Embedding model: {settings.embedding_model}")
print(f"Embedding dimensions: {settings.embedding_dimensions}")
print(f"Incidents: {incidents}")
print(f"Documents: {documents}")
print(f"Chunks: {chunks}")
print(f"Vector extension enabled: {vector_enabled}")

if incidents == 0:
    raise SystemExit("ERROR: No hay incidents cargados")
if documents == 0:
    raise SystemExit("ERROR: No hay documents cargados")
if chunks == 0:
    raise SystemExit("ERROR: No hay chunks en el indice")
if not vector_enabled:
    raise SystemExit("ERROR: La extension vector no esta habilitada")
' "${env_pairs[@]}" || exit 1

print_ok "Variables minimas y base de datos verificadas"
print_ok "La demo puede arrancar con este entorno"
