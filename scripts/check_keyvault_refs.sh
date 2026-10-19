#!/usr/bin/env bash
# Verifica que las referencias Key Vault de la Web App y de las Function Apps
# se han resuelto. Usa la API de App Service
#   GET <site>/config/configreferences/appsettings?api-version=2022-03-01
# que devuelve el ESTADO de cada referencia (nunca el valor del secreto).
# Solo imprime: app, setting, secreto, estado y, si falla, el detalle del error.
#
# Uso:
#   ./scripts/check_keyvault_refs.sh            # comprueba
#   ./scripts/check_keyvault_refs.sh --restart  # reinicia las apps antes (fuerza re-resolucion)
set -euo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

RESTART=false
for arg in "$@"; do
  case "${arg}" in
    --restart) RESTART=true ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) fail "Argumento no soportado: ${arg}. Usa --restart o --help" ;;
  esac
done

load_azure_env
azure_login_check
require_vars AZURE_RESOURCE_GROUP
load_bicep_outputs

API_VERSION="2022-03-01"
total_bad=0
total_refs=0

check_site() {
  local kind="$1"
  local resource_group="$2"
  local app_name="$3"
  local site_id
  local rows
  local name status secret details

  site_id="$(az "${kind}" show --resource-group "${resource_group}" --name "${app_name}" --query id -o tsv | tr -d '\r\n')"
  [[ -n "${site_id}" ]] || fail "No se encontro ${kind} '${app_name}'"

  if [[ "${RESTART}" == "true" ]]; then
    az "${kind}" restart --resource-group "${resource_group}" --name "${app_name}" -o none
  fi

  # La respuesta solo contiene metadatos (reference, status, vaultName,
  # secretName, details...). Se filtran las columnas con --query.
  rows="$(az rest \
    --method get \
    --url "https://management.azure.com${site_id}/config/configreferences/appsettings?api-version=${API_VERSION}" \
    --query "value[].[name, properties.secretName, properties.status, properties.details]" \
    -o tsv | tr -d '\r')"

  echo "== ${app_name}"
  if [[ -z "${rows}" ]]; then
    echo "   (sin referencias Key Vault)"
    return
  fi
  while IFS=$'\t' read -r name secret status details; do
    [[ -n "${name}" ]] || continue
    name="${name##*/}"
    total_refs=$((total_refs + 1))
    if [[ "${status}" == "Resolved" ]]; then
      printf '   %-40s %-28s %s\n' "${name}" "${secret}" "${status}"
    else
      total_bad=$((total_bad + 1))
      printf '   %-40s %-28s %s  %s\n' "${name}" "${secret}" "${status:-Unknown}" "${details:-}"
    fi
  done <<<"${rows}"
}

check_site webapp "${AZURE_RESOURCE_GROUP}" "${AZURE_WEBAPP_NAME}"
check_site functionapp "${AZURE_FUNCTIONS_RESOURCE_GROUP}" "${AZURE_INDEXER_FUNCTION_NAME}"
check_site functionapp "${AZURE_FUNCTIONS_RESOURCE_GROUP}" "${AZURE_INCIDENTS_FUNCTION_NAME}"

echo
if (( total_bad > 0 )); then
  echo "ERROR: ${total_bad}/${total_refs} referencias sin resolver." >&2
  echo "Pistas: SecretNotFound -> ./scripts/sync_keyvault_secrets.sh; AccessToKeyVaultDenied/MSINotEnabled -> revisa identidad y rol Key Vault Secrets User (la propagacion RBAC puede tardar minutos); despues ./scripts/check_keyvault_refs.sh --restart" >&2
  exit 1
fi
echo "OK: ${total_refs} referencias Key Vault resueltas."
