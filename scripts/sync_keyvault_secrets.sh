#!/usr/bin/env bash
# Sube los secretos de .env.azure a Azure Key Vault sin exponer sus valores.
#
# Garantias:
#   - Ningun valor se pasa como argumento a un proceso (argv visible en `ps`):
#     se escribe con `printf` (builtin de bash) a un fichero temporal 0600 dentro
#     de un directorio 0700 y se sube con `az keyvault secret set --file`.
#   - El fichero temporal se borra siempre (trap EXIT/INT/TERM).
#   - Todas las llamadas a az usan `-o none` o capturan la salida sin imprimirla.
#   - Solo se imprimen NOMBRES de variables/secretos, nunca valores.
#   - Es un script no interactivo: no escribe en el historial de la shell.
#
# Uso:
#   ./scripts/sync_keyvault_secrets.sh            # sincroniza
#   ./scripts/sync_keyvault_secrets.sh --dry-run  # solo lista nombres y accion prevista
#   ./scripts/sync_keyvault_secrets.sh --force    # reescribe aunque el valor no haya cambiado
set -euo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

# Todo fichero creado por este script es privado (0600/0700).
umask 077

DRY_RUN=false
FORCE=false
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true ;;
    -h|--help)
      sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) fail "Argumento no soportado: ${arg}. Usa --dry-run, --force o --help" ;;
  esac
done

log() {
  echo "[kv-sync] $*"
}

warn() {
  echo "[kv-sync] AVISO: $*" >&2
}

load_azure_env

SECRET_VARS=("${KV_REQUIRED_SECRET_VARS[@]}" "${KV_OPTIONAL_SECRET_VARS[@]}" "${KV_STORED_ONLY_SECRET_VARS[@]}")

TMP_DIR=""
cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# Resuelve el valor de una variable en la variable global SECRET_VALUE (nunca a stdout).
SECRET_VALUE=""
resolve_secret_value() {
  local env_name="$1"
  SECRET_VALUE=""
  if [[ "${env_name}" == "DATABASE_URL" ]]; then
    if [[ -n "${POSTGRES_ADMIN_PASSWORD:-}" && -n "${AZURE_POSTGRES_SERVER_NAME:-}" ]] \
      && ! is_placeholder_secret "${POSTGRES_ADMIN_PASSWORD}"; then
      SECRET_VALUE="$(build_database_url)"
      return
    fi
    SECRET_VALUE="${DATABASE_URL:-}"
    return
  fi
  SECRET_VALUE="${!env_name:-}"
}

if [[ "${DRY_RUN}" == "true" ]]; then
  log "Modo --dry-run: no se contacta con Azure ni se escriben secretos."
  log "Key Vault destino: ${KEY_VAULT_NAME:-<se leera del output keyVaultName del deployment>}"
  for env_name in "${SECRET_VARS[@]}"; do
    kv_name="$(kv_secret_name "${env_name}")"
    if [[ "${env_name}" == "DATABASE_URL" ]]; then
      if [[ -n "${POSTGRES_ADMIN_PASSWORD:-}" ]] && ! is_placeholder_secret "${POSTGRES_ADMIN_PASSWORD}"; then
        log "  ${env_name} -> ${kv_name}: se construira desde POSTGRES_* y el servidor del deployment"
      elif ! is_placeholder_secret "${DATABASE_URL:-}"; then
        log "  ${env_name} -> ${kv_name}: se subiria (valor de .env.azure)"
      else
        log "  ${env_name} -> ${kv_name}: se omitiria (vacio o placeholder)"
      fi
      continue
    fi
    if is_placeholder_secret "${!env_name:-}"; then
      log "  ${env_name} -> ${kv_name}: se omitiria (vacio o placeholder)"
    else
      log "  ${env_name} -> ${kv_name}: se subiria"
    fi
  done
  exit 0
fi

use_key_vault || fail "USE_KEY_VAULT no esta activo; no hay nada que sincronizar."
azure_login_check
require_vars AZURE_RESOURCE_GROUP
load_bicep_outputs
require_vars KEY_VAULT_NAME

rbac_enabled="$(az keyvault show --name "${KEY_VAULT_NAME}" --query "properties.enableRbacAuthorization" -o tsv 2>/dev/null | tr -d '\r\n' || true)"
[[ -n "${rbac_enabled}" ]] || fail "No se encuentra el Key Vault '${KEY_VAULT_NAME}' o no tienes acceso. Ejecuta antes ./scripts/deploy_infra.sh"
[[ "${rbac_enabled}" == "true" ]] || warn "El vault '${KEY_VAULT_NAME}' no usa RBAC (enableRbacAuthorization=${rbac_enabled})."

TMP_DIR="$(make_private_tmpdir)"
SECRET_FILE="${TMP_DIR}/value"
SECRET_FILE_NATIVE="$(native_path "${SECRET_FILE}")"

# Escribe el secreto desde fichero, reintentando por si el rol RBAC recien
# asignado aun no se ha propagado (puede tardar unos minutos).
kv_set_from_file() {
  local kv_name="$1"
  local attempt
  for attempt in 1 2 3 4 5; do
    if az keyvault secret set \
      --vault-name "${KEY_VAULT_NAME}" \
      --name "${kv_name}" \
      --file "${SECRET_FILE_NATIVE}" \
      --encoding utf-8 \
      -o none 2>"${TMP_DIR}/az.err"; then
      return 0
    fi
    if grep -qi "forbidden\|ForbiddenByRbac\|not authorized" "${TMP_DIR}/az.err"; then
      warn "Sin permisos aun sobre '${KEY_VAULT_NAME}' (intento ${attempt}/5). Esperando propagacion RBAC..."
      sleep 30
      continue
    fi
    if grep -qi "deleted but recoverable\|ObjectIsDeletedButRecoverable" "${TMP_DIR}/az.err"; then
      fail "El secreto '${kv_name}' esta en soft-delete. Recuperalo con: az keyvault secret recover --vault-name ${KEY_VAULT_NAME} --name ${kv_name}"
    fi
    # az no conoce el valor (va por fichero), asi que su error no lo contiene.
    cat "${TMP_DIR}/az.err" >&2
    return 1
  done
  return 1
}

# Compara con el valor actual sin imprimirlo. Devuelve 0 si es identico.
kv_value_unchanged() {
  local kv_name="$1"
  local current
  current="$(az keyvault secret show \
    --vault-name "${KEY_VAULT_NAME}" \
    --name "${kv_name}" \
    --query value \
    -o tsv 2>/dev/null | tr -d '\r' || true)"
  if [[ -n "${current}" && "${current}" == "${SECRET_VALUE}" ]]; then
    current=""
    return 0
  fi
  current=""
  return 1
}

updated=0
unchanged=0
skipped=0
failed=0

log "Sincronizando secretos en Key Vault '${KEY_VAULT_NAME}'"
for env_name in "${SECRET_VARS[@]}"; do
  kv_name="$(kv_secret_name "${env_name}")"
  resolve_secret_value "${env_name}"

  if is_placeholder_secret "${SECRET_VALUE}"; then
    if [[ " ${KV_REQUIRED_SECRET_VARS[*]} " == *" ${env_name} "* ]]; then
      warn "${env_name} esta vacio o es un placeholder; se omite (es OBLIGATORIO para la app)."
    else
      warn "${env_name} esta vacio o es un placeholder; se omite."
    fi
    skipped=$((skipped + 1))
    SECRET_VALUE=""
    continue
  fi

  if [[ "${FORCE}" != "true" ]] && kv_value_unchanged "${kv_name}"; then
    log "  = ${env_name} -> ${kv_name} (sin cambios)"
    unchanged=$((unchanged + 1))
    SECRET_VALUE=""
    continue
  fi

  # printf es builtin: el valor no aparece en argv de ningun proceso.
  printf '%s' "${SECRET_VALUE}" > "${SECRET_FILE}"
  SECRET_VALUE=""
  if kv_set_from_file "${kv_name}"; then
    log "  + ${env_name} -> ${kv_name} (actualizado)"
    updated=$((updated + 1))
  else
    warn "No se pudo escribir ${kv_name}"
    failed=$((failed + 1))
  fi
  : > "${SECRET_FILE}"
  rm -f "${SECRET_FILE}"
done

log "Resumen: ${updated} actualizados, ${unchanged} sin cambios, ${skipped} omitidos, ${failed} con error."
(( failed == 0 )) || exit 1
log "Siguiente paso: ./scripts/configure_app_settings.sh && ./scripts/check_keyvault_refs.sh"
