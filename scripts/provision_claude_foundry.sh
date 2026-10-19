#!/usr/bin/env bash
# Crea (si no existe) un recurso Foundry (AIServices) en Sweden Central y despliega Claude Haiku 5.5.
# Idempotente. No imprime claves.
#
# Variables opcionales:
#   AZURE_RESOURCE_GROUP        (por defecto rg-internal-assistant-demo)
#   FOUNDRY_RESOURCE_NAME       (por defecto logiassist-ai-swc; tambien es el subdominio)
#   FOUNDRY_LOCATION            (por defecto swedencentral, unica region UE con Claude)
#   CLAUDE_MODEL_NAME           (por defecto claude-haiku-5-5)
#   CLAUDE_MODEL_VERSION        (por defecto 2 = "Hosted on Azure")
#   CLAUDE_DEPLOYMENT_NAME      (por defecto igual al modelo; es el CHAT_MODEL de la app)
#   CLAUDE_DEPLOYMENT_CAPACITY  (por defecto 20, dentro de la cuota de la suscripcion)
# Obligatorias para modelos de Anthropic (datos del alta en Azure Marketplace, modelProviderData):
#   CLAUDE_ORG_NAME             nombre de tu organizacion
#   CLAUDE_INDUSTRY             sector en minusculas (p. ej. technology)
#   CLAUDE_COUNTRY_CODE         pais en ISO de 2 letras (p. ej. ES)
set -euo pipefail

AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-internal-assistant-demo}"
FOUNDRY_RESOURCE_NAME="${FOUNDRY_RESOURCE_NAME:-logiassist-ai-swc}"
FOUNDRY_LOCATION="${FOUNDRY_LOCATION:-swedencentral}"
CLAUDE_MODEL_NAME="${CLAUDE_MODEL_NAME:-claude-haiku-5-5}"
CLAUDE_MODEL_VERSION="${CLAUDE_MODEL_VERSION:-2}"
CLAUDE_DEPLOYMENT_NAME="${CLAUDE_DEPLOYMENT_NAME:-${CLAUDE_MODEL_NAME}}"
CLAUDE_DEPLOYMENT_CAPACITY="${CLAUDE_DEPLOYMENT_CAPACITY:-20}"

echo "Cuenta Azure: $(az account show --query user.name -o tsv) / $(az account show --query name -o tsv)"

if az cognitiveservices account show -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" -o none 2>/dev/null; then
  echo "Recurso ${FOUNDRY_RESOURCE_NAME} ya existe."
else
  echo "Creando recurso AIServices ${FOUNDRY_RESOURCE_NAME} en ${FOUNDRY_LOCATION}..."
  az cognitiveservices account create \
    --name "${FOUNDRY_RESOURCE_NAME}" \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --location "${FOUNDRY_LOCATION}" \
    --kind AIServices \
    --sku S0 \
    --custom-domain "${FOUNDRY_RESOURCE_NAME}" \
    --yes -o none
fi

if az cognitiveservices account deployment show -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" \
  --deployment-name "${CLAUDE_DEPLOYMENT_NAME}" -o none 2>/dev/null; then
  echo "Deployment ${CLAUDE_DEPLOYMENT_NAME} ya existe."
else
  : "${CLAUDE_ORG_NAME:?Define CLAUDE_ORG_NAME}" "${CLAUDE_INDUSTRY:?Define CLAUDE_INDUSTRY}" "${CLAUDE_COUNTRY_CODE:?Define CLAUDE_COUNTRY_CODE}"
  echo "Desplegando ${CLAUDE_MODEL_NAME} v${CLAUDE_MODEL_VERSION} (GlobalStandard, capacidad ${CLAUDE_DEPLOYMENT_CAPACITY})..."
  # `az cognitiveservices account deployment create` aun no admite modelProviderData: se usa la API REST.
  account_id="$(az cognitiveservices account show -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query id -o tsv | tr -d '\r')"
  body="$(python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"sku":{"name":"GlobalStandard","capacity":int(a[4])},"properties":{"model":{"format":"Anthropic","name":a[1],"version":a[2]},"modelProviderData":{"industry":a[5],"organizationName":a[6],"countryCode":a[7]}}}))' \
    "${CLAUDE_MODEL_NAME}" "${CLAUDE_MODEL_VERSION}" "_" "${CLAUDE_DEPLOYMENT_CAPACITY}" "${CLAUDE_INDUSTRY}" "${CLAUDE_ORG_NAME}" "${CLAUDE_COUNTRY_CODE}")"
  az rest --method put \
    --url "https://management.azure.com${account_id}/deployments/${CLAUDE_DEPLOYMENT_NAME}?api-version=2025-10-01-preview" \
    --headers "Content-Type=application/json" \
    --body "${body}" -o none
  for _ in $(seq 1 30); do
    state="$(az cognitiveservices account deployment show -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" \
      --deployment-name "${CLAUDE_DEPLOYMENT_NAME}" --query properties.provisioningState -o tsv 2>/dev/null | tr -d '\r')"
    [[ "${state}" == "Succeeded" || "${state}" == "Failed" ]] && break
    sleep 10
  done
fi

az cognitiveservices account deployment show -n "${FOUNDRY_RESOURCE_NAME}" -g "${AZURE_RESOURCE_GROUP}" \
  --deployment-name "${CLAUDE_DEPLOYMENT_NAME}" \
  --query "{deployment:name, model:properties.model.name, version:properties.model.version, sku:sku.name, state:properties.provisioningState}" -o table

cat <<EOF

Configuracion para la app:
  LLM_PROVIDER=anthropic_foundry
  EMBEDDINGS_PROVIDER=openai
  CHAT_MODEL=${CLAUDE_DEPLOYMENT_NAME}
  ANTHROPIC_FOUNDRY_RESOURCE=${FOUNDRY_RESOURCE_NAME}
  ANTHROPIC_FOUNDRY_API_KEY=<az cognitiveservices account keys list -n ${FOUNDRY_RESOURCE_NAME} -g ${AZURE_RESOURCE_GROUP}>
EOF
