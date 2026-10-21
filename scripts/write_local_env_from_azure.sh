#!/usr/bin/env bash
# Genera un .env local (permisos 600) para ejecutar la app o la evaluacion contra Claude Haiku 5.5
# en Foundry, reutilizando las claves ya configuradas en la Web App desplegada.
# Los valores secretos van de `az` directamente al fichero: nunca se imprimen.
#
# Uso: scripts/write_local_env_from_azure.sh [ruta_salida] (por defecto .env)
# Variables opcionales: AZURE_RESOURCE_GROUP, AZURE_WEBAPP_NAME, FOUNDRY_RESOURCE_NAME,
#   CLAUDE_DEPLOYMENT_NAME, LOCAL_DATABASE_URL.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-${ROOT_DIR}/.env}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-internal-assistant-demo}"
AZURE_WEBAPP_NAME="${AZURE_WEBAPP_NAME:-internal-assistant-demo-web}"
FOUNDRY_RESOURCE_NAME="${FOUNDRY_RESOURCE_NAME:-logiassist-ai-swc}"
CLAUDE_DEPLOYMENT_NAME="${CLAUDE_DEPLOYMENT_NAME:-claude-haiku-5-5}"
LOCAL_DATABASE_URL="${LOCAL_DATABASE_URL:-postgresql+psycopg://assistant:assistant@localhost:5433/assistant}"

if [[ -e "${OUTPUT}" && "${FORCE:-false}" != "true" ]]; then
  echo "ERROR: ${OUTPUT} ya existe. Usa FORCE=true para sobrescribirlo." >&2
  exit 1
fi

umask 077
tmp="$(mktemp "${OUTPUT}.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT

webapp_setting() {
  az webapp config appsettings list -g "${AZURE_RESOURCE_GROUP}" -n "${AZURE_WEBAPP_NAME}" \
    --query "[?name=='$1'].value | [0]" -o tsv 2>/dev/null | tr -d '\r\n'
}

{
  echo "APP_ENV=local"
  echo "APP_NAME=internal-assistant-mvp"
  echo "DATABASE_URL=${LOCAL_DATABASE_URL}"
  echo "LLM_PROVIDER=anthropic_foundry"
  echo "EMBEDDINGS_PROVIDER=openai"
  echo "CHAT_MODEL=${CLAUDE_DEPLOYMENT_NAME}"
  echo "LLM_EFFORT=low"
  echo "EMBEDDING_MODEL=$(webapp_setting EMBEDDING_MODEL)"
  echo "EMBEDDING_DIMENSIONS=$(webapp_setting EMBEDDING_DIMENSIONS)"
  echo "ANTHROPIC_FOUNDRY_RESOURCE=${FOUNDRY_RESOURCE_NAME}"
  printf 'ANTHROPIC_FOUNDRY_API_KEY=%s\n' "$(az cognitiveservices account keys list -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query key1 -o tsv | tr -d '\r\n')"
  printf 'OPENAI_API_KEY=%s\n' "$(webapp_setting OPENAI_API_KEY)"
  for name in RERANKER_ENABLED RERANKER_PROVIDER RERANKER_BASE_URL RERANKER_MODEL RERANKER_CANDIDATES RERANKER_TIMEOUT_SECONDS \
    RERANKER_API_KEY LANGSMITH_TRACING LANGSMITH_API_KEY LANGSMITH_PROJECT LANGSMITH_ENDPOINT; do
    printf '%s=%s\n' "${name}" "$(webapp_setting "${name}")"
  done
  echo "EVAL_MAX_CONCURRENCY=4"
} >"${tmp}"

# Comprueba que lo imprescindible no ha quedado vacio, mostrando solo nombres.
missing=()
for name in OPENAI_API_KEY ANTHROPIC_FOUNDRY_API_KEY EMBEDDING_MODEL; do
  grep -q "^${name}=." "${tmp}" || missing+=("${name}")
done
if ((${#missing[@]})); then
  echo "ERROR: no se pudieron obtener: ${missing[*]}" >&2
  exit 1
fi
grep -q '^OPENAI_API_KEY=@Microsoft.KeyVault' "${tmp}" && {
  echo "ERROR: la Web App ya usa referencias Key Vault; obten los secretos del vault." >&2
  exit 1
}

mv "${tmp}" "${OUTPUT}"
trap - EXIT
chmod 600 "${OUTPUT}"
echo "Escrito ${OUTPUT} (600) con: $(cut -d= -f1 "${OUTPUT}" | tr '\n' ' ')"
