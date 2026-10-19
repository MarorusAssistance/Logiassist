#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env
azure_login_check
require_vars PROJECT_NAME ENVIRONMENT_NAME AZURE_LOCATION AZURE_RESOURCE_GROUP POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD POSTGRES_DATABASE_NAME CHAT_MODEL EMBEDDING_MODEL EMBEDDING_DIMENSIONS MICROSOFT_APP_ID

ALLOWED_ORIGINS_JSON="$(csv_to_json_array "${ALLOWED_ORIGINS:-}")"
ENABLE_APPLICATION_INSIGHTS="${ENABLE_APPLICATION_INSIGHTS:-true}"
AZURE_FUNCTIONS_RESOURCE_GROUP="${AZURE_FUNCTIONS_RESOURCE_GROUP:-${AZURE_RESOURCE_GROUP}-functions}"
DEPLOYMENT_NAME="${PROJECT_NAME}-${ENVIRONMENT_NAME}-infra"
TEMPLATE_FILE="$(native_path "${ROOT_DIR}/infra/main.bicep")"

if use_key_vault; then
  USE_KEY_VAULT_PARAM=true
  # Quien despliega recibe "Key Vault Secrets Officer" para poder ejecutar
  # sync_keyvault_secrets.sh. Si inicias sesion con un service principal,
  # define KEY_VAULT_ADMIN_PRINCIPAL_ID y KEY_VAULT_ADMIN_PRINCIPAL_TYPE=ServicePrincipal.
  if [[ -z "${KEY_VAULT_ADMIN_PRINCIPAL_ID:-}" ]]; then
    KEY_VAULT_ADMIN_PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null | tr -d '\r\n' || true)"
  fi
  if [[ -z "${KEY_VAULT_ADMIN_PRINCIPAL_ID}" ]]; then
    echo "AVISO: no se pudo obtener tu object id; no se asignara Key Vault Secrets Officer. Define KEY_VAULT_ADMIN_PRINCIPAL_ID en .env.azure." >&2
  fi
else
  USE_KEY_VAULT_PARAM=false
  KEY_VAULT_ADMIN_PRINCIPAL_ID=""
  require_vars OPENAI_API_KEY ADMIN_API_KEY APP_SHARED_SECRET MICROSOFT_APP_PASSWORD
fi

az group create --name "${AZURE_RESOURCE_GROUP}" --location "${AZURE_LOCATION}" >/dev/null
az group create --name "${AZURE_FUNCTIONS_RESOURCE_GROUP}" --location "${AZURE_LOCATION}" >/dev/null

# Los parametros @secure() van en un fichero JSON privado (0600, borrado al
# salir) en lugar de en argv, donde serian visibles con `ps`. Los valores se
# envian a Python por stdin (separados por NUL) con printf, que es builtin.
umask 077
SECRETS_TMP_DIR="$(make_private_tmpdir)"
trap 'rm -rf "${SECRETS_TMP_DIR}"' EXIT
SECURE_PARAMS_FILE="${SECRETS_TMP_DIR}/secure.parameters.json"
printf '%s\0' \
  "${USE_KEY_VAULT_PARAM}" \
  "${POSTGRES_ADMIN_PASSWORD}" \
  "${OPENAI_API_KEY:-}" \
  "${ADMIN_API_KEY:-}" \
  "${APP_SHARED_SECRET:-}" \
  "${MICROSOFT_APP_PASSWORD:-}" \
  | "${PYTHON_CMD[@]}" -c '
import json, sys

fields = sys.stdin.buffer.read().decode("utf-8").split("\0")
use_kv, pg_password, openai_key, admin_key, shared_secret, bot_password = fields[:6]
params = {"postgresAdminPassword": {"value": pg_password}}
if use_kv != "true":
    params.update({
        "openAiApiKey": {"value": openai_key},
        "adminApiKey": {"value": admin_key},
        "appSharedSecret": {"value": shared_secret},
        "microsoftAppPassword": {"value": bot_password},
    })
document = {
    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
    "contentVersion": "1.0.0.0",
    "parameters": params,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(document, handle)
' "$(native_path "${SECURE_PARAMS_FILE}")"
chmod 600 "${SECURE_PARAMS_FILE}"

az deployment group create \
  --name "${DEPLOYMENT_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --template-file "${TEMPLATE_FILE}" \
  --parameters "@$(native_path "${SECURE_PARAMS_FILE}")" \
  --parameters \
    projectName="${PROJECT_NAME}" \
    environmentName="${ENVIRONMENT_NAME}" \
    location="${AZURE_LOCATION}" \
    functionsResourceGroupName="${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
    postgresAdminUser="${POSTGRES_ADMIN_USER}" \
    postgresDatabaseName="${POSTGRES_DATABASE_NAME}" \
    chatModel="${CHAT_MODEL}" \
    embeddingModel="${EMBEDDING_MODEL}" \
    embeddingDimensions="${EMBEDDING_DIMENSIONS}" \
    microsoftAppId="${MICROSOFT_APP_ID}" \
    allowedOrigins="${ALLOWED_ORIGINS_JSON}" \
    enableApplicationInsights="${ENABLE_APPLICATION_INSIGHTS}" \
    useKeyVault="${USE_KEY_VAULT_PARAM}" \
    keyVaultName="${KEY_VAULT_NAME:-}" \
    keyVaultAdminPrincipalId="${KEY_VAULT_ADMIN_PRINCIPAL_ID}" \
    keyVaultAdminPrincipalType="${KEY_VAULT_ADMIN_PRINCIPAL_TYPE:-User}" >/dev/null
rm -rf "${SECRETS_TMP_DIR}"

load_bicep_outputs
CLIENT_IP="$(current_public_ip | tr -d '\r\n')"
if [[ -z "$(az postgres flexible-server firewall-rule list \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_POSTGRES_SERVER_NAME}" \
  --query "[?name=='local-client'].name" \
  -o tsv 2>/dev/null)" ]]; then
  az postgres flexible-server firewall-rule create \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_POSTGRES_SERVER_NAME}" \
    --rule-name "local-client" \
    --start-ip-address "${CLIENT_IP}" \
    --end-ip-address "${CLIENT_IP}" >/dev/null
fi

echo "Infra desplegada: ${AZURE_RESOURCE_GROUP}"
echo "Web App: ${AZURE_WEBAPP_NAME}"
echo "Indexer Function: ${AZURE_INDEXER_FUNCTION_NAME}"
echo "Incidents Function: ${AZURE_INCIDENTS_FUNCTION_NAME}"
echo "PostgreSQL: ${AZURE_POSTGRES_SERVER_NAME}"
if use_key_vault; then
  echo "Key Vault: ${KEY_VAULT_NAME}"
  echo "Siguiente paso: ./scripts/sync_keyvault_secrets.sh"
fi
