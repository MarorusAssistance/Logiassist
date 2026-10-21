#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

# USE_KEY_VAULT=true (por defecto): los secretos se configuran como referencias
#   @Microsoft.KeyVault(VaultName=<kv>;SecretName=<nombre>) que la identidad
#   administrada de cada app resuelve en runtime. Requiere haber ejecutado antes
#   ./scripts/sync_keyvault_secrets.sh. Ningun valor secreto pasa por argv.
# USE_KEY_VAULT=false: comportamiento anterior (valores en claro en App Settings).

load_azure_env
azure_login_check
require_vars PROJECT_NAME ENVIRONMENT_NAME AZURE_RESOURCE_GROUP CHAT_MODEL EMBEDDING_MODEL EMBEDDING_DIMENSIONS MICROSOFT_APP_ID
load_bicep_outputs

if use_key_vault; then
  require_vars KEY_VAULT_NAME
  EXISTING_KV_SECRETS="$(kv_existing_secret_names)" || fail "No se pudieron listar los secretos de '${KEY_VAULT_NAME}'. Revisa tu rol Key Vault Secrets Officer."
  missing_required=()
  for env_name in "${KV_REQUIRED_SECRET_VARS[@]}"; do
    if ! grep -qx "$(kv_secret_name "${env_name}")" <<<"${EXISTING_KV_SECRETS}"; then
      missing_required+=("${env_name}")
    fi
  done
  if (( ${#missing_required[@]} > 0 )); then
    fail "Faltan secretos en Key Vault para: ${missing_required[*]}. Ejecuta ./scripts/sync_keyvault_secrets.sh"
  fi
  # Opcionales: referencia solo si el secreto existe; si no, valor vacio (una
  # referencia rota haria que la app recibiera el literal @Microsoft.KeyVault(...)).
  secret_setting() {
    local env_name="$1"
    if grep -qx "$(kv_secret_name "${env_name}")" <<<"${EXISTING_KV_SECRETS}"; then
      kv_reference "${env_name}"
    else
      printf '\n'
    fi
  }
  DATABASE_URL="$(secret_setting DATABASE_URL)"
  OPENAI_API_KEY="$(secret_setting OPENAI_API_KEY)"
  ADMIN_API_KEY="$(secret_setting ADMIN_API_KEY)"
  APP_SHARED_SECRET="$(secret_setting APP_SHARED_SECRET)"
  MICROSOFT_APP_PASSWORD="$(secret_setting MICROSOFT_APP_PASSWORD)"
  RERANKER_API_KEY="$(secret_setting RERANKER_API_KEY)"
  LANGSMITH_API_KEY="$(secret_setting LANGSMITH_API_KEY)"
  ANTHROPIC_FOUNDRY_API_KEY="$(secret_setting ANTHROPIC_FOUNDRY_API_KEY)"
  AZURE_AI_API_KEY="$(secret_setting AZURE_AI_API_KEY)"
  echo "Usando referencias Key Vault (${KEY_VAULT_NAME}) para: ${KV_REQUIRED_SECRET_VARS[*]} ${KV_OPTIONAL_SECRET_VARS[*]}"
else
  echo "AVISO: USE_KEY_VAULT=false; los secretos se guardan en claro en App Settings." >&2
  require_vars POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD POSTGRES_DATABASE_NAME OPENAI_API_KEY ADMIN_API_KEY APP_SHARED_SECRET MICROSOFT_APP_PASSWORD
  DATABASE_URL="$(build_database_url)"
fi
MICROSOFT_APP_TENANT_ID="${MICROSOFT_APP_TENANT_ID:-$(az account show --query tenantId -o tsv | tr -d '\r\n')}"
BOT_ENDPOINT="${BOT_ENDPOINT:-https://${AZURE_WEBAPP_HOSTNAME}/api/messages}"
CUSTOM_INCIDENTS_API_BASE_URL="${CUSTOM_INCIDENTS_API_BASE_URL:-https://${AZURE_INCIDENTS_FUNCTION_HOSTNAME}/api}"
INDEXER_API_BASE_URL="${INDEXER_API_BASE_URL:-https://${AZURE_INDEXER_FUNCTION_HOSTNAME}/api}"
DEFAULT_ALLOWED_ORIGINS="https://teams.microsoft.com,https://${AZURE_WEBAPP_HOSTNAME}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-${DEFAULT_ALLOWED_ORIGINS}}"
APP_ENV="${APP_ENV:-demo}"
APP_NAME="${APP_NAME:-internal-assistant-mvp}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Trazas extendidas por peticion en Blob Storage (la Web App escribe con su identidad administrada).
TRACE_STORE="${TRACE_STORE:-azure_blob}"
TRACE_STORE_ACCOUNT_URL="${TRACE_STORE_ACCOUNT_URL:-https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net}"
TRACE_STORE_CONTAINER="${TRACE_STORE_CONTAINER:-rag-traces}"
# Las Functions solo generan embeddings: no necesitan el proveedor de chat de Claude.
FUNCTIONS_LLM_PROVIDER="${LLM_PROVIDER:-openai}"
if [[ "${FUNCTIONS_LLM_PROVIDER}" == "anthropic_foundry" ]]; then
  FUNCTIONS_LLM_PROVIDER="${EMBEDDINGS_PROVIDER:-openai}"
fi

az webapp config appsettings set \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_WEBAPP_NAME}" \
  --settings \
    APP_ENV="${APP_ENV}" \
    APP_NAME="${APP_NAME}" \
    DATABASE_URL="${DATABASE_URL}" \
    LLM_PROVIDER="${LLM_PROVIDER:-openai}" \
    EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-openai}" \
    OPENAI_API_KEY="${OPENAI_API_KEY}" \
    CHAT_MODEL="${CHAT_MODEL}" \
    LLM_EFFORT="${LLM_EFFORT:-low}" \
    ANTHROPIC_FOUNDRY_RESOURCE="${ANTHROPIC_FOUNDRY_RESOURCE:-}" \
    ANTHROPIC_FOUNDRY_API_KEY="${ANTHROPIC_FOUNDRY_API_KEY:-}" \
    AZURE_AI_RESOURCE="${AZURE_AI_RESOURCE:-}" \
    AZURE_AI_API_KEY="${AZURE_AI_API_KEY:-}" \
    EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
    EMBEDDING_DIMENSIONS="${EMBEDDING_DIMENSIONS}" \
    RERANKER_ENABLED="${RERANKER_ENABLED:-false}" \
    RERANKER_PROVIDER="${RERANKER_PROVIDER:-http}" \
    RERANKER_BASE_URL="${RERANKER_BASE_URL:-http://127.0.0.1:8091}" \
    RERANKER_MODEL="${RERANKER_MODEL:-BAAI/bge-reranker-v2-m3}" \
    RERANKER_API_KEY="${RERANKER_API_KEY:-}" \
    RERANKER_TIMEOUT_SECONDS="${RERANKER_TIMEOUT_SECONDS:-30}" \
    RERANKER_CANDIDATES="${RERANKER_CANDIDATES:-100}" \
    RETRIEVAL_CONTEXT_MAX_TOKENS="${RETRIEVAL_CONTEXT_MAX_TOKENS:-16000}" \
    TITLE_MODEL="${TITLE_MODEL:-gpt-5-nano}" \
    TITLE_REASONING_EFFORT="${TITLE_REASONING_EFFORT:-minimal}" \
    LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-}" \
    TRACE_STORE="${TRACE_STORE}" \
    TRACE_STORE_ACCOUNT_URL="${TRACE_STORE_ACCOUNT_URL}" \
    TRACE_STORE_CONTAINER="${TRACE_STORE_CONTAINER}" \
    ADMIN_API_KEY="${ADMIN_API_KEY}" \
    APP_SHARED_SECRET="${APP_SHARED_SECRET}" \
    APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING:-}" \
    LANGSMITH_TRACING="${LANGSMITH_TRACING:-false}" \
    LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}" \
    LANGSMITH_PROJECT="${LANGSMITH_PROJECT:-internal-assistant-mvp}" \
    LANGSMITH_ENDPOINT="${LANGSMITH_ENDPOINT:-https://api.smith.langchain.com}" \
    ALLOWED_ORIGINS="${ALLOWED_ORIGINS}" \
    BOT_ENDPOINT="${BOT_ENDPOINT}" \
    CUSTOM_INCIDENTS_API_BASE_URL="${CUSTOM_INCIDENTS_API_BASE_URL}" \
    INDEXER_API_BASE_URL="${INDEXER_API_BASE_URL}" \
    MICROSOFT_APP_ID="${MICROSOFT_APP_ID}" \
    MICROSOFT_APP_PASSWORD="${MICROSOFT_APP_PASSWORD}" \
    MICROSOFT_APP_TENANT_ID="${MICROSOFT_APP_TENANT_ID}" \
    TEAMS_APP_ID="${TEAMS_APP_ID:-}" \
    LOG_LEVEL="${LOG_LEVEL}" \
    PYTHONPATH="/home/site/wwwroot:/home/site/wwwroot/src" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
    WEBSITE_RUN_FROM_PACKAGE="0" >/dev/null

az webapp config set \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_WEBAPP_NAME}" \
  --startup-file "python3 -m gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:\$PORT --timeout 600 --access-logfile - --error-logfile - --chdir app-service main:app" >/dev/null

for function_app in "${AZURE_INDEXER_FUNCTION_NAME}" "${AZURE_INCIDENTS_FUNCTION_NAME}"; do
  az functionapp config appsettings set \
    --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
    --name "${function_app}" \
    --settings \
      APP_ENV="${APP_ENV}" \
      APP_NAME="${APP_NAME}" \
      DATABASE_URL="${DATABASE_URL}" \
      LLM_PROVIDER="${FUNCTIONS_LLM_PROVIDER}" \
      EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-openai}" \
      OPENAI_API_KEY="${OPENAI_API_KEY}" \
      AZURE_AI_RESOURCE="${AZURE_AI_RESOURCE:-}" \
      AZURE_AI_API_KEY="${AZURE_AI_API_KEY:-}" \
      CHAT_MODEL="${CHAT_MODEL}" \
      EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
      EMBEDDING_DIMENSIONS="${EMBEDDING_DIMENSIONS}" \
      RERANKER_ENABLED="${RERANKER_ENABLED:-false}" \
      RERANKER_PROVIDER="${RERANKER_PROVIDER:-http}" \
      RERANKER_BASE_URL="${RERANKER_BASE_URL:-http://127.0.0.1:8091}" \
      RERANKER_MODEL="${RERANKER_MODEL:-BAAI/bge-reranker-v2-m3}" \
      RERANKER_API_KEY="${RERANKER_API_KEY:-}" \
      RERANKER_TIMEOUT_SECONDS="${RERANKER_TIMEOUT_SECONDS:-30}" \
      RERANKER_CANDIDATES="${RERANKER_CANDIDATES:-30}" \
      ADMIN_API_KEY="${ADMIN_API_KEY}" \
      APP_SHARED_SECRET="${APP_SHARED_SECRET}" \
      APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING:-}" \
      CUSTOM_INCIDENTS_API_BASE_URL="${CUSTOM_INCIDENTS_API_BASE_URL}" \
      INDEXER_API_BASE_URL="${INDEXER_API_BASE_URL}" \
      LOG_LEVEL="${LOG_LEVEL}" \
      PYTHONPATH="/home/site/wwwroot:/home/site/wwwroot/src:/home/site/wwwroot/.python_packages/lib/site-packages" \
      SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
      ENABLE_ORYX_BUILD="true" \
      AzureWebJobsFeatureFlags="EnableWorkerIndexing" >/dev/null
done

echo "App settings configuradas"
