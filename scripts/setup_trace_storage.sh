#!/usr/bin/env bash
# Prepara el almacen de trazas extendidas en Azure sin redesplegar toda la infraestructura:
# identidad administrada de la Web App, contenedor privado, borrado a los 90 dias y roles.
# Idempotente: se puede ejecutar varias veces.
set -euo pipefail

RG="${AZURE_RESOURCE_GROUP:-rg-internal-assistant-demo}"
WEBAPP="${AZURE_WEBAPP_NAME:-internal-assistant-demo-web}"
SA="${AZURE_STORAGE_ACCOUNT_NAME:-st7dhz4rbt6pa6o}"
CONTAINER="${TRACE_STORE_CONTAINER:-rag-traces}"
RETENTION_DAYS="${TRACE_RETENTION_DAYS:-90}"

echo "Identidad administrada de ${WEBAPP}..."
WEBAPP_PRINCIPAL_ID="$(az webapp identity assign -g "${RG}" -n "${WEBAPP}" --query principalId -o tsv)"

echo "Contenedor ${CONTAINER} en ${SA}..."
az storage container create --account-name "${SA}" --name "${CONTAINER}" --auth-mode login --public-access off -o none

echo "Borrado automatico a los ${RETENTION_DAYS} dias..."
POLICY_FILE="$(mktemp)"
cat >"${POLICY_FILE}" <<JSON
{"rules":[{"name":"expire-rag-traces","enabled":true,"type":"Lifecycle","definition":{
  "filters":{"blobTypes":["blockBlob"],"prefixMatch":["${CONTAINER}/"]},
  "actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":${RETENTION_DAYS}}}}}}]}
JSON
az storage account management-policy create -g "${RG}" --account-name "${SA}" --policy "@${POLICY_FILE}" -o none
rm -f "${POLICY_FILE}"

SCOPE="$(az storage account show -g "${RG}" -n "${SA}" --query id -o tsv)/blobServices/default/containers/${CONTAINER}"
echo "Rol Storage Blob Data Contributor para la Web App..."
az role assignment create --assignee-object-id "${WEBAPP_PRINCIPAL_ID}" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "${SCOPE}" -o none
echo "Rol Storage Blob Data Reader para tu usuario (leer trazas con scripts/show_trace.py --blob)..."
az role assignment create --assignee-object-id "$(az ad signed-in-user show --query id -o tsv)" --assignee-principal-type User \
  --role "Storage Blob Data Reader" --scope "${SCOPE}" -o none

echo "Listo. Cuenta: https://${SA}.blob.core.windows.net  contenedor: ${CONTAINER}"
