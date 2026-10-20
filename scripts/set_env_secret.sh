#!/usr/bin/env bash
# Guarda un secreto en .env sin mostrarlo en pantalla ni dejarlo en el historial.
# Uso: ./scripts/set_env_secret.sh VOYAGE_API_KEY
set -euo pipefail

NAME="${1:?Uso: $0 NOMBRE_VARIABLE}"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"

read -r -s -p "Valor de ${NAME} (no se muestra): " VALUE
echo
[[ -n "${VALUE}" ]] || { echo "Valor vacio; no se cambia nada." >&2; exit 1; }

touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
TMP="$(mktemp)"
grep -v "^${NAME}=" "${ENV_FILE}" >"${TMP}" || true
printf '%s=%s\n' "${NAME}" "${VALUE}" >>"${TMP}"
cat "${TMP}" >"${ENV_FILE}"
rm -f "${TMP}"
echo "${NAME} guardado en ${ENV_FILE}"
