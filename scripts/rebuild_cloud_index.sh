#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env
azure_login_check
load_bicep_outputs
export DATABASE_URL="$(webapp_appsetting DATABASE_URL)"
export APP_ENV="${APP_ENV:-$(webapp_appsetting APP_ENV)}"
export LLM_PROVIDER="${LLM_PROVIDER:-$(webapp_appsetting LLM_PROVIDER)}"
export EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-$(webapp_appsetting EMBEDDINGS_PROVIDER)}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$(webapp_appsetting OPENAI_API_KEY)}"
export CHAT_MODEL="${CHAT_MODEL:-$(webapp_appsetting CHAT_MODEL)}"
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-$(webapp_appsetting EMBEDDING_MODEL)}"
export EMBEDDING_DIMENSIONS="${EMBEDDING_DIMENSIONS:-$(webapp_appsetting EMBEDDING_DIMENSIONS)}"
REBUILD_SCRIPT="${ROOT_DIR}/scripts/rebuild_index.py"

run_uv_python_script \
  "${REBUILD_SCRIPT}" \
  "DATABASE_URL=${DATABASE_URL}" \
  "APP_ENV=${APP_ENV}" \
  "LLM_PROVIDER=${LLM_PROVIDER}" \
  "EMBEDDINGS_PROVIDER=${EMBEDDINGS_PROVIDER}" \
  "OPENAI_API_KEY=${OPENAI_API_KEY}" \
  "CHAT_MODEL=${CHAT_MODEL}" \
  "EMBEDDING_MODEL=${EMBEDDING_MODEL}" \
  "EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS}"
